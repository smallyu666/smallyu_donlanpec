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
    dwg_path = os.path.abspath("法兰凸-覆层.dwg")
    if not os.path.exists(dwg_path):
        return None

    # === 解析默认保存目录（项目需求表->项目保存路径） ===
    save_dir = get_project_save_dir(product_id)
    if not save_dir:
        save_dir = os.path.join(os.getcwd(), "exports")
        os.makedirs(save_dir, exist_ok=True)

    # === 关键修复 1：模板 → 唯一目标副本 ===
    target_dwg = copy_template_to_target(dwg_path, save_dir, flange)

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
    # 通用函数：修改文字对象
        # 通用函数：修改文字对象
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

    def modify_by_handle(doc, handle, new_text, retries=5, delay=0.5):
        doc = get_current_doc()
        if doc is None:
            print("⚠️ 未获取当前文档")
            return False

        safe_text = str(new_text).replace("\r", "").replace("\n", "").replace("\t", "")

        # ⚡ 强制刷新文档状态
        try:
            doc.Regen()
        except:
            pass

        time.sleep(0.2)  # 等待 COM 稳定

        for attempt in range(retries):
            try:
                obj = doc.HandleToObject(handle)
                if obj is None:
                    print(f"⚠️ Handle {handle} 不存在，第 {attempt + 1} 次重试...")
                    time.sleep(delay)
                    continue

                if obj.ObjectName in ("AcDbText", "AcDbMText"):
                    old = obj.TextString
                    obj.TextString = safe_text
                    print(f"✅ 修改成功: '{old}' → '{safe_text}' (Handle {handle})")
                    return True

                elif "Dimension" in obj.ObjectName:
                    old = obj.TextOverride
                    obj.TextOverride = safe_text
                    print(f"✅ 修改成功: '{old}' → '{safe_text}' (Handle {handle})")
                    return True

                else:
                    print(f"⚠️ Handle {handle} 类型不支持修改: {obj.ObjectName}")
                    return False

            except Exception as e:
                print(f"⚠️ 第 {attempt + 1} 次修改失败: {e}")
                time.sleep(delay)

        print(f"❌ 修改失败: {handle}")
        return False

    # 初始化 AutoCAD
    # extract_text(doc)

    # 处理产品法规 → 替换到 handle 77872
    regulation_text = get_flang_jisuan_value(product_id, "法兰名义外径", flange)
    if regulation_text:
        safe_modify(doc,"15D0", regulation_text)
        # modify_by_handle(doc,"12EC", regulation_text)

    regulation_text = get_flang_jisuan_value(product_id, "法兰名义内径", flange)
    if regulation_text:
        safe_modify(doc,"15CF", regulation_text)
        # modify_by_handle(doc,"1342", regulation_text)

    regulation_text = get_flang_jisuan_value(product_id, "法兰名义内径", flange)
    regulation_text2 = get_flang_jisuan_value(product_id, "法兰颈部小端名义厚度", flange)
    regulation_text = float(regulation_text)+2*float(regulation_text2)
    if regulation_text:
        safe_modify(doc,"15D3", regulation_text)
        # modify_by_handle(doc,"1378", regulation_text)

    regulation_text = get_flang_jisuan_value(product_id, "法兰名义内径", flange)
    regulation_text2 = get_flang_jisuan_value(product_id, "法兰颈部大端名义厚度", flange)
    regulation_text = float(regulation_text)+2*float(regulation_text2)
    if regulation_text:
        safe_modify(doc,"15DB", regulation_text)
        # modify_by_handle(doc,"1377", regulation_text)

    regulation_text = get_flang_jisuan_value(product_id, "法兰名义厚度", flange)
    if regulation_text:
        safe_modify(doc,"15D4", regulation_text)
        # modify_by_handle(doc,"1313", regulation_text)

    regulation_text = get_flang_jisuan_value(product_id, "法兰颈部高度", flange)
    if regulation_text:
        safe_modify(doc,"15D5", regulation_text)
        # modify_by_handle(doc,"13AA", regulation_text)

    regulation_text = get_flang_jisuan_value(product_id, "法兰总高", flange)
    if regulation_text:
        safe_modify(doc,"15D6", regulation_text)
        # modify_by_handle(doc,"12EA", regulation_text)
    regulation_text = get_flang_jisuan_value(product_id, "螺栓数量", flange)
    regulation_text2 = get_flang_jisuan_value(product_id, "螺栓根径", flange)
    regulation_text = f"{regulation_text}-∅{round(float(regulation_text2))}"
    # modify_by_handle(doc, "1345", regulation_text)

    regulation_text = get_flang_jisuan_value(product_id, "螺栓数量", flange)
    if regulation_text:
        safe_modify(doc,"15D8", regulation_text)

    regulation_text = get_flang_jisuan_value(product_id, "螺栓孔直径", flange)
    if regulation_text:
        safe_modify(doc,"15D9", round(float(regulation_text)))
    regulation_text = get_flang_jisuan_value(product_id, "法兰直边段高度", flange)
    if regulation_text:
        safe_modify(doc,"15DE", regulation_text)
        # modify_by_handle(doc,"13AB", regulation_text)

    regulation_text = get_flang_jisuan_value(product_id, "螺栓中心圆直径", flange)
    if regulation_text:
        safe_modify(doc,"15D1", regulation_text)
        regulation_text = f"∅{regulation_text}"
        # modify_by_handle(doc,"12ED", regulation_text)

    if regulation_text:
        safe_modify(doc, "15D6", regulation_text)

    regulation_text = get_flang_jisuan_value(product_id, "法兰总高", flange)
    if regulation_text:
        safe_modify(doc,"15D6", regulation_text)

        # modify_by_handle(doc,"12EA", regulation_text)

    safe_modify(doc, "17AE", flange)
    safe_modify(doc, "17FA", flange)
    # # === 查询凹槽深度 ===
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
    # cursor.close()
    # conn.close()
    #
    # if row:
    #     groove_depth = str(row[0])
    #     modify_by_handle(doc, "1314", groove_depth)
    #     print(f"✅ 已更新 Handle=1314 为凹槽深度: {groove_depth}")
    # else:
    #     print(f"⚠️ 未找到 {flange} 的凹槽深度")

    # # === 查询覆层厚度 ===
    # conn = pymysql.connect(
    #     host="localhost",
    #     user="root",
    #     password="123456",
    #     database="产品设计活动库",
    #     charset="utf8mb4"
    # )
    # cursor = conn.cursor()
    # sql = """
    #     SELECT 参数值
    #     FROM 产品设计活动表_元件附加参数表
    #     WHERE 产品ID=%s AND 元件名称=%s AND 参数名称='覆层厚度'
    # """
    # cursor.execute(sql, (product_id, flange))
    # row = cursor.fetchone()
    # cursor.close()
    # conn.close()
    #
    # if row:
    #     cover_thickness = str(row[0])
    #     modify_by_handle(doc, "140C", cover_thickness)
    #     modify_by_handle(doc, "1376", cover_thickness)
    #     print(f"✅ 已更新 Handle=140C/18ED 覆层厚度: {cover_thickness}")
    # else:
    #     print(f"⚠️ 未找到 {flange} 覆层厚度")
    #
    # # === 继续 CAD 操作 ===
