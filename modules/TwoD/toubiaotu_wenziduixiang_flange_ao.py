import datetime
import json
import re
import shutil
import subprocess
import tempfile
import time
import traceback

import chardet
import configparser

import openpyxl
from pyautocad import Autocad
import pymysql

from modules.TwoD.TwoD_peizhi import get_project_save_dir, get_autocad_instance, _find_open_doc, wait_cad_idle, \
    refresh_doc, open_drawing_with_wait, ensure_dwg_path, must_exist_file, safe_modify, copy_template_to_target
from modules.TwoD.toubiaotu_biaozhu import extract_dimensions
from modules.chanpinguanli.chanpinguanli_main import product_manager

import win32com.client
import os

from modules.wenbenshengcheng import cunguige
from modules.wenbenshengcheng.cunguige import get_value, load_json_data
from modules.wenbenshengcheng.generate_material_list import generate_material_list



def twoDgeneration(product_id, flange):
    acad = get_autocad_instance()
    if acad is None:
        print("❌ 没有可用的 AutoCAD 实例")
        return
    template_path = os.path.abspath("法兰-凹.dwg")
    if not os.path.exists(template_path):
        return None

    # === 解析默认保存目录（项目需求表->项目保存路径） ===
    save_dir = get_project_save_dir(product_id)
    if not save_dir:
        save_dir = os.path.join(os.getcwd(), "exports")
        os.makedirs(save_dir, exist_ok=True)

    # === 关键修复 1：模板 → 唯一目标副本 ===
    target_dwg = copy_template_to_target(template_path, save_dir, flange)

    # === 关键修复 2：关闭 AutoCAD 的“未命名临时图纸” ===
    try:
        docs_to_close = []
        for i in range(acad.Documents.Count):
            doc_i = acad.Documents.Item(i)
            name = (doc_i.Name or "").lower()
            full = (doc_i.FullName or "").strip()
            # Drawing1 / Drawing2 / 无 FullName 的
            if name.startswith("drawing") and (not full):
                docs_to_close.append(doc_i)
        for d in docs_to_close:
            try:
                d.Close(False)
            except Exception:
                pass
    except Exception:
        pass

    # === 关键修复 3：等待 AutoCAD 空闲，再打开目标文件 ===
    wait_cad_idle(acad, timeout=10)
    acad, doc = open_drawing_with_wait(target_dwg)
    if doc is None:
        print("❌ 打开目标图失败:", target_dwg)
        return None

    # === 关键修复 4：打开后再等一次空闲 + 刷新视图 ===
    wait_cad_idle(acad, timeout=30)
    refresh_doc(doc)
    print(f"✅ 成功打开唯一副本: {target_dwg}")

    # 这里就可以安全提取文字、图元
    # for ent in acad.iter_objects("Text"):
    #     print("文字内容:", ent.TextString)
    def get_flange_value(product_id, param_name, flange_name):
        connection = pymysql.connect(
            host='localhost',
            user='root',
            password='123456',
            database='产品设计活动库',
            charset='utf8mb4'
        )
        with connection.cursor() as cursor:
            sql = """
                SELECT `参数值`
                FROM `产品设计活动表_元件附加参数表`
                WHERE `产品ID` = %s AND `参数名称`=%s AND `元件名称` = %s
            """
            cursor.execute(sql, (product_id, param_name ,flange_name))
            row = cursor.fetchone()
            if row:
                return str(row[0] or "")
            else:
                print(f"未找到 产品ID={product_id} 法兰={flange_name}")
                return "", ""

    def get_flang_jisuan_value(product_id, param_name, flange_name):
        connection = pymysql.connect(
            host='localhost',
            user='root',
            password='123456',
            database='产品设计活动库',
            charset='utf8mb4'
        )
        with connection.cursor() as cursor:
            sql = """
                        SELECT `Value`
                        FROM `产品设计活动表_元件计算结果表`
                        WHERE `产品ID` = %s AND `元件名称`=%s AND `Name` = %s
                        LIMIT 1
                    """
            cursor.execute(sql, (product_id, flange_name, param_name ))
            row = cursor.fetchone()
            if row:
                return str(row[0] or "")
            else:
                print(f"未找到 产品ID={product_id} 法兰={flange_name}")
                return ""


    def extract_text(doc, retries=10, delay=1):
        print("【文字对象】提取中...")
        for attempt in range(retries):
            try:
                for obj in doc.ModelSpace:
                    if obj.ObjectName in ['AcDbText', 'AcDbMText']:
                        print(
                            f"{obj.ObjectName}: '{obj.TextString}' 位置: {obj.InsertionPoint} 图层: {obj.Layer} Handle: {obj.Handle}")
                return  # 成功就返回
            except Exception as e:
                print(f"⚠️ 第 {attempt + 1} 次尝试失败: {e}")
                time.sleep(delay)
        print("❌ 超过最大尝试次数，无法访问 ModelSpace")

    def get_obj_safe(doc, handle, retries=3, delay=1):
        """通过 Handle 安全获取对象"""
        for attempt in range(retries):
            try:
                obj = doc.HandleToObject(handle)
                if obj:
                    return obj
            except Exception as e:
                print(f"⚠️ 第 {attempt + 1} 次尝试获取 Handle {handle} 失败: {e}")
            time.sleep(delay)
        print(f"❌ Handle {handle} 最终无法获取")
        return None

    # 通用函数：修改文字对象（支持 Text, MText, Dimension, Attribute, 有 Value 属性的对象）
    def get_current_doc():
        acad = Autocad(create_if_not_exists=True)  # ⚡ 每次都创建新的 COM 对象
        try:
            return acad.doc
        except Exception as e:
            print(f"⚠️ 获取当前文档失败: {e}")
            return None


    # 初始化 AutoCAD
    # extract_text(doc)

    # 处理产品法规 → 替换到 handle 77872
    regulation_text = get_flang_jisuan_value(product_id, "法兰名义外径", flange)
    if regulation_text:
        safe_modify(doc,"15AE", regulation_text)
        # safe_modify(doc,"12DE", regulation_text)

    regulation_text = get_flang_jisuan_value(product_id, "法兰名义内径", flange)
    if regulation_text:
        safe_modify(doc,"15AD", regulation_text)
        regulation_text = f"∅{regulation_text}"
        # safe_modify(doc,"1302", regulation_text)

    regulation_text = get_flang_jisuan_value(product_id, "法兰名义内径", flange)
    regulation_text2 = get_flang_jisuan_value(product_id, "法兰颈部小端名义厚度", flange)
    regulation_text = float(regulation_text)+2*float(regulation_text2)
    if regulation_text:
        safe_modify(doc,"15B2", regulation_text)
        regulation_text = f"∅{regulation_text}"
        # modify_by_handle(doc,"1309", regulation_text)
    regulation_text = get_flang_jisuan_value(product_id, "D2", flange)
    safe_modify(doc, "130f3", regulation_text)
    regulation_text = get_flang_jisuan_value(product_id, "D3", flange)
    safe_modify(doc, "130fa", regulation_text)

    regulation_text = get_flang_jisuan_value(product_id, "法兰名义内径", flange)
    regulation_text2 = get_flang_jisuan_value(product_id, "法兰颈部大端名义厚度", flange)
    regulation_text = float(regulation_text)+2*float(regulation_text2)
    if regulation_text:
        safe_modify(doc,"15B3", regulation_text)
        regulation_text = f"∅{regulation_text}"
        # modify_by_handle(doc,"12E8", regulation_text)

    regulation_text = get_flang_jisuan_value(product_id, "法兰名义厚度", flange)
    if regulation_text:
        safe_modify(doc,"15B4", regulation_text)
        # modify_by_handle(doc,"1312", regulation_text)

    regulation_text = get_flang_jisuan_value(product_id, "法兰颈部高度", flange)
    if regulation_text:
        safe_modify(doc,"15B5", regulation_text)
        # modify_by_handle(doc,"12FD", regulation_text)

    regulation_text = get_flang_jisuan_value(product_id, "法兰总高", flange)
    if regulation_text:
        safe_modify(doc,"15B6", regulation_text)
        # modify_by_handle(doc,"12DB", regulation_text)
    regulation_text = get_flang_jisuan_value(product_id, "螺栓数量", flange)
    regulation_text2 = get_flang_jisuan_value(product_id, "螺栓根径", flange)
    regulation_text = f"{regulation_text}-∅{round(float(regulation_text2))}"
    # modify_by_handle(doc, "1335", regulation_text)

    regulation_text = get_flang_jisuan_value(product_id, "螺栓数量", flange)
    if regulation_text:
        safe_modify(doc,"15B8", regulation_text)

    regulation_text = get_flang_jisuan_value(product_id, "螺栓孔直径", flange)
    if regulation_text:
        safe_modify(doc,"15B9", round(float(regulation_text)))
    regulation_text = get_flang_jisuan_value(product_id, "法兰直边段高度", flange)
    if regulation_text:
        safe_modify(doc,"15C9", regulation_text)
        # modify_by_handle(doc,"1378", regulation_text)

    regulation_text = get_flang_jisuan_value(product_id, "螺栓中心圆直径", flange)
    if regulation_text:
        safe_modify(doc,"15AF", regulation_text)
        regulation_text = f"∅{regulation_text}"
        print("regulationtest", regulation_text)
        # modify_by_handle(doc,"12DF", regulation_text)

    safe_modify(doc, "18CB", flange)
    safe_modify(doc, "1B11", flange)
    print(f"🔹 法兰 {flange} 生成完成，准备返回 COM 对象")
    return doc


    # regulation_text = get_flang_jisuan_value(product_id, "法兰总高", flange)
    # if regulation_text:
        # modify_by_handle(doc,"12DB", regulation_text)
    # extract_dimensions(doc)

    # # === 连接数据库 ===
    # conn = pymysql.connect(
    #     host="localhost",
    #     user="root",
    #     password="123456",
    #     database="产品设计活动库",
    #     charset="utf8mb4"
    # )
    # cursor = conn.cursor()

    # # === 查询凹槽深度 ===
    # sql = """
    #     SELECT 参数值
    #     FROM 产品设计活动表_元件附加参数表
    #     WHERE 产品ID=%s AND 元件名称=%s AND 参数名称='凹槽深度'
    # """
    # cursor.execute(sql, (product_id, flange))
    # row = cursor.fetchone()
    # conn.close()
    #
    # if row:
    #     groove_depth = str(row[0])  # 取出凹槽深度
    #     # === 修改 CAD 文字 ===
    #     modify_by_handle(doc, "1315", groove_depth)
    #     print(f"✅ 已更新 Handle=1315 为凹槽深度: {groove_depth}")
    # else:
    #     print(f"⚠️ 未找到 {flange} 的凹槽深度")

