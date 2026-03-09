import datetime
import os
import shutil
import subprocess
import sys
import time
import tkinter as tk
import uuid
from tkinter import filedialog

import psutil
import win32com

import os
import time, pythoncom
import pywintypes  # 来自 pywin32

# 这些 HRESULT 是常见的“忙/稍后再试/被拒绝”
_RPC_BUSY_CODES = {
    -2147417846,   # 常见：消息筛选器显示应用程序正在使用中
    -2147417830,   # RPC_E_SERVERCALL_RETRYLATER
    -2147418111,   # RPC_E_CALL_REJECTED（偶尔也会遇到）
}

def com_retry(callable_fn, retries=30, base_sleep=0.05, max_sleep=0.5, quiet=False):
    """
    在 AutoCAD 繁忙时对 COM 调用做自动重试。
    - retries: 最大重试次数
    - base_sleep: 初始重试间隔
    - max_sleep: 最大间隔上限
    """
    for i in range(retries):
        try:
            return callable_fn()
        except pywintypes.com_error as e:
            # 取 HRESULT
            hr = getattr(e, "hresult", None)
            if hr is None:
                # 有些版本把 hr 放在 args[0]
                try:
                    hr = e.args[0]
                except Exception:
                    hr = None

            busy = (hr in _RPC_BUSY_CODES) or ("应用程序正在使用中" in str(e))
            if not busy:
                # 非忙类错误，直接抛出
                raise

            # 忙：泵消息 + 渐进等待后重试
            try:
                pythoncom.PumpWaitingMessages()
            except Exception:
                pass

            # 线性退避
            slp = min(max_sleep, base_sleep * (i + 1))
            if not quiet:
                # 你也可以换成 log_info
                print(f"⌛ AutoCAD 繁忙，重试第 {i+1}/{retries} 次，等待 {slp:.2f}s …")
            time.sleep(slp)
            continue

def open_drawing_with_wait(file_path, timeout=300):
    """用 COM 打开 DWG，并等待文档就绪后返回 (acad, doc)。不走 os.startfile / SendCommand。"""
    import time, pythoncom, os

    # 1) 规范化 & 必须是存在的 .dwg 文件（绝对路径）
    path = ensure_dwg_path(file_path)
    if not must_exist_file(path):
        print(f"❌ DWG 文件不存在：{path}")
        return None, None

    # 2) 获取 AutoCAD 实例
    acad = get_autocad_instance()
    if acad is None:
        return None, None

    # ★ 打开前先等一下空闲（降低“忙”概率）
    wait_cad_idle(acad, timeout=10)

    # 3) 已开即用（避免重复打开）
    def _norm(p): return os.path.normcase(os.path.normpath(p or ""))
    target = _norm(path)
    try:
        # ★ 遍历也可能被“忙”拒绝，用 com_retry 包起来
        def _scan():
            for i in range(acad.Documents.Count):
                d = acad.Documents.Item(i)
                if _norm(getattr(d, "FullName", "")) == target:
                    return d
            return None
        d0 = com_retry(_scan, retries=10, base_sleep=0.05, max_sleep=0.3, quiet=True)
        if d0 is not None:
            try:
                com_retry(lambda: d0.Activate(), retries=10, base_sleep=0.05, max_sleep=0.3, quiet=True)
            except Exception:
                pass
            return acad, d0
    except Exception:
        pass

    # 4) 只用 COM 打开（关键！）—— ★ 用 com_retry 包裹
    try:
        doc = com_retry(lambda: acad.Documents.Open(path), retries=40, base_sleep=0.05, max_sleep=0.5)
    except Exception as e:
        print(f"⚠️ COM Open 失败：{e}")
        return None, None

    # 5) 等待就绪（消息泵 + 轻量轮询）
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            pythoncom.PumpWaitingMessages()
        except Exception:
            pass
        try:
            # ★ 访问 Name/ModelSpace 也用 com_retry 防忙
            _ = com_retry(lambda: getattr(doc, "Name", None), retries=10, base_sleep=0.05, max_sleep=0.3, quiet=True)
            _ = com_retry(lambda: getattr(doc, "ModelSpace", None), retries=10, base_sleep=0.05, max_sleep=0.3, quiet=True)
            break  # 可用了
        except Exception:
            time.sleep(0.05)
    return acad, doc

def strip_quotes(p: str) -> str:
    p = (p or "").strip()
    if len(p) >= 2 and ((p[0]==p[-1]=='"') or (p[0]==p[-1]=="'")):
        return p[1:-1]
    return p

def ensure_dwg_path(p: str) -> str:
    p = os.path.normpath(strip_quotes(p))
    if not p:
        return ""
    if os.path.isdir(p):
        return ""  # 传的是目录就直接判错
    root, ext = os.path.splitext(p)
    p = p if ext.lower() == ".dwg" else (root + ".dwg")
    return os.path.abspath(p)   # ★ 一律转为绝对路径

def must_exist_file(p: str) -> bool:
    try:
        return os.path.isfile(p)
    except Exception:
        return False

def choose_cad_exe():
    root = tk.Tk()
    root.withdraw()
    exe_path = filedialog.askopenfilename(
        title="选择 AutoCAD 程序 (acad.exe)",
        filetypes=[("AutoCAD 可执行文件", "*.exe"), ("所有文件", "*.*")]
    )
    root.destroy()  # 🔥 非常关键，显式销毁 Tk 实例
    return exe_path if exe_path else None
def get_current_doc():
    """返回当前活动文档（不会新开 AutoCAD 进程）"""
    try:
        import win32com.client
        acad = win32com.client.GetActiveObject("AutoCAD.Application")
        return acad.ActiveDocument if acad else None
    except Exception as e:
        print(f"⚠️ 获取当前文档失败: {e}")
        return None
def try_start_autocad_com():
    import time, pythoncom
    import win32com.client

    # 1) 先附着已运行实例（避免多开导致“看起来重复打开”）
    try:
        acad = win32com.client.GetActiveObject("AutoCAD.Application")
        acad.Visible = True
        print("✅ 附着已有 AutoCAD")
        return acad
    except Exception:
        pass

    # 2) 尝试通用 ProgID（会起最新版本）
    try:
        acad = win32com.client.gencache.EnsureDispatch("AutoCAD.Application")
        acad.Visible = True
        print("✅ 通过通用 ProgID 连接 AutoCAD")
        return acad
    except Exception:
        pass

    # 3) 逐个尝试版本化 ProgID（兼容 10 及以上所有版本号）
    #    经验：2025≈28，2024≈27 … 但为“未来可用”，范围给到 40→10
    for ver in range(40, 9, -1):  # 40,39,...,10
        progid = f"AutoCAD.Application.{ver}"
        try:
            acad = win32com.client.gencache.EnsureDispatch(progid)
            acad.Visible = True
            print(f"✅ 通过 {progid} 连接 AutoCAD")
            # 小等候：部分版本刚启动时 COM 属性未就绪
            t0 = time.time()
            while time.time() - t0 < 5:
                try:
                    _ = acad.Version  # 触发一次属性访问
                    break
                except Exception:
                    pythoncom.PumpWaitingMessages()
                    time.sleep(0.05)
            return acad
        except Exception:
            continue

    print("⚠️ COM 启动失败：未找到可用的 AutoCAD ProgID（10+）")
    return None

def start_autocad_by_path(exe_path):
    if not os.path.exists(exe_path):
        print("❌ 无效的 AutoCAD 路径")
        return None
    subprocess.Popen([exe_path])
    print(f"🚀 已手动启动 AutoCAD: {exe_path}")
    # ⚠️ 注意：这里要等 CAD 启动完成再去连 COM
    time.sleep(2)
    return try_start_autocad_com()
def get_autocad_instance():
    # 1️⃣ 尝试直接通过 COM 连接
    acad = try_start_autocad_com()
    if acad is not None:
        return acad

    # 2️⃣ 检查是否已有 AutoCAD 进程（不重复弹窗）
    for proc in psutil.process_iter(['name']):
        if proc.info['name'] and 'acad.exe' in proc.info['name'].lower():
            print("⚙️ 检测到 AutoCAD 已在运行，等待 COM 接口可用...")
            time.sleep(2)
            return try_start_autocad_com()

    # 3️⃣ 若系统中确实没有 CAD，再询问用户选择路径（仅第一次需要）
    exe_path = choose_cad_exe()
    if exe_path is None:
        print("⚠️ 未选择 AutoCAD 程序，退出。")
        return None
# === [NEW] 通过 产品ID -> 项目ID -> 项目保存路径 ===
def get_project_save_dir(product_id: str) -> str:
    """
    返回：项目保存路径（确保已存在），失败则返回空字符串。
    约定：
      - 产品需求库：表 `产品需求表`，字段：产品ID, 项目ID
      - 项目需求库：表 `项目需求表`，字段：项目ID, 项目保存路径
    如你的真实表名/字段名不同，只需改下面 SQL 即可。
    """
    import pymysql, os

    if not product_id:
        return ""

    # 这些库名/表名/字段名根据你的实际情况改一改就行
    DB_PROD_REQ = "产品需求库"
    TBL_PROD_REQ = "产品需求表"      # 映射 产品ID -> 项目ID
    COL_PROD_ID  = "产品ID"
    COL_PROJ_ID  = "项目ID"

    DB_PROJ_REQ = "项目需求库"
    TBL_PROJ_REQ = "项目需求表"      # 包含 项目保存路径
    COL_PROJ_SAVE = "项目保存路径"

    proj_id = None
    save_dir = ""

    # === 1) 先用产品ID查项目ID ===
    try:
        conn1 = pymysql.connect(
            host="localhost", port=3306, user="root", password="123456",
            database=DB_PROD_REQ, charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor
        )
        with conn1.cursor() as cur:
            sql = f"SELECT {COL_PROJ_ID} FROM {TBL_PROD_REQ} WHERE {COL_PROD_ID}=%s LIMIT 1"
            cur.execute(sql, (str(product_id),))
            row = cur.fetchone()
            if row:
                proj_id = str(row.get(COL_PROJ_ID) or "").strip()
        conn1.close()
    except Exception as e:
        proj_id = None

    if not proj_id:
        return ""

    # === 2) 用项目ID查项目保存路径 ===
    try:
        conn2 = pymysql.connect(
            host="localhost", port=3306, user="root", password="123456",
            database=DB_PROJ_REQ, charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor
        )
        with conn2.cursor() as cur:
            sql = f"SELECT {COL_PROJ_SAVE} FROM {TBL_PROJ_REQ} WHERE {COL_PROJ_ID}=%s LIMIT 1"
            cur.execute(sql, (proj_id,))
            row = cur.fetchone()
            if row:
                save_dir = str(row.get(COL_PROJ_SAVE) or "").strip()
        conn2.close()
    except Exception as e:
        save_dir = ""

    # === 3) 路径存在性与创建 ===
    try:
        if save_dir:
            save_dir = save_dir.strip().strip('\'"')
            # 先规范化
            save_dir = os.path.normpath(save_dir)
            # 不是绝对路径 → 以程序所在目录为基准转绝对路径
            if not os.path.isabs(save_dir):
                base_dir = os.path.abspath(os.path.dirname(sys.argv[0]))  # 程序安装目录
                save_dir = os.path.normpath(os.path.join(base_dir, save_dir))
            os.makedirs(save_dir, exist_ok=True)
    except Exception as e:
        save_dir = ""
    print("保存路径：",save_dir)
    return save_dir
def wait_cad_idle(acad, timeout=10, poll=0.05):
    """等待 AutoCAD 空闲（不同版本均可用）。"""
    import time, pythoncom
    t0 = time.time()
    state = None
    try:
        state = acad.GetAcadState()  # 新版本支持 .IsQuiescent
    except Exception:
        state = None
    while time.time() - t0 < timeout:
        try:
            if state is not None:
                if state.IsQuiescent:
                    return True
            else:
                # 通用兜底：消息泵 + 小睡
                pythoncom.PumpWaitingMessages()
        except Exception:
            pass
        time.sleep(poll)
    return False

def refresh_doc(doc):
    """强制刷新视图/数据库，避免看不到更新或后续操作挂起。"""
    try:
        acAllViewports = 1
        doc.Regen(acAllViewports)
    except Exception:
        pass
    try:
        doc.Update()
    except Exception:
        pass
def _norm(p):
    return os.path.normcase(os.path.normpath(p or ""))

def _find_open_doc(acad, full_path):
    target = _norm(full_path)
    for i in range(acad.Documents.Count):
        try:
            d = acad.Documents.Item(i)
            if _norm(getattr(d, "FullName", "")) == target:
                return d
        except Exception:
            pass
    return None


def modify_text_by_handle(doc, handle, new_text, retries=5, delay=0.5):
    if doc is None:
        doc = get_current_doc()
        print("⚠️ 未获取当前文档")
        return False

    safe_text = str(new_text).replace("\r", "").replace("\n", "").replace("\t", "")

    # 强制刷新文档状态
    try:
        doc.Regen()
    except:
        pass

    # 等待 COM 对象稳定
    time.sleep(0.2)

    for attempt in range(retries):
        try:
            obj = doc.HandleToObject(handle)
            if obj is None:
                print(f"⚠️ Handle {handle} 不存在，第 {attempt + 1} 次重试...")
                time.sleep(delay)
                continue

            if hasattr(obj, 'TextString'):
                old = obj.TextString
                obj.TextString = safe_text
                print(f"✅ 修改成功: Handle {handle} → '{safe_text}'")
                return True
            elif hasattr(obj, 'Value'):
                old = obj.Value
                obj.Value = safe_text
                print(f"✅ 修改成功: Handle {handle} → '{safe_text}'")
                return True
            else:
                print(f"⚠️ Handle {handle} 对象不支持 TextString/Value")
                return False

        except Exception as e:
            print(f"⚠️ 第 {attempt + 1} 次修改失败: {e}")
            time.sleep(delay)

    print(f"❌ 修改失败: {handle}")
    return False


def safe_modify(doc, handle, value):
    """如果 value 没有值，则替换成 '/'，再修改句柄"""
    if value in (None, "", "None"):
        value = "/"
    modify_text_by_handle(doc, handle, str(value))
def make_unique_dwg_path(save_dir, base_name):
    # 例：BEU投标图_20251017_153010_123_3E2F.dwg
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    uid = uuid.uuid4().hex[:4].upper()
    fn = f"{base_name}_{ts}_{uid}.dwg"
    p = os.path.abspath(os.path.join(save_dir, fn))
    return p

def copy_template_to_target(template_path, save_dir, base_name):
    template_path = os.path.abspath(template_path)
    if not os.path.isfile(template_path):
        raise FileNotFoundError(f"模板不存在: {template_path}")

    os.makedirs(save_dir, exist_ok=True)
    target = make_unique_dwg_path(save_dir, base_name)

    # 防止目标已存在（极端碰撞）
    while os.path.exists(target):
        target = make_unique_dwg_path(save_dir, base_name)

    shutil.copy2(template_path, target)
    # 防模板携带的“只读/归档”等属性影响
    try:
        os.chmod(target, 0o666)
    except Exception:
        pass
    return target