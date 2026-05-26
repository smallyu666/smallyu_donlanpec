# -*- coding: utf-8 -*-
# 0509新修改--项目路径变更处理
"""启动时校验上次项目在本机的磁盘路径；失效时引导用户重新指定并同步数据库。"""
import os

import modules.chanpinguanli.bianl as bianl
import modules.chanpinguanli.common_usage as common_usage
from PyQt5.QtWidgets import QFileDialog, QMessageBox


def get_project_root_folder(project_info):
    """根据项目需求表一行记录，得到「业主_项目名」文件夹绝对路径。"""
    if not project_info:
        return None
    save_path = str(project_info.get("项目保存路径") or "").strip()
    owner = str(project_info.get("业主名称") or "").strip()
    name = str(project_info.get("项目名称") or "").strip()
    if not save_path or not owner or not name:
        return None
    return os.path.normpath(os.path.join(save_path, f"{owner}_{name}"))


def disk_matches_project(project_id, project_root_folder):
    """项目根目录存在且 id.csv 中的 ID 与 project_id 一致。"""
    if not project_root_folder or not project_id:
        return False
    csv_path = os.path.join(project_root_folder, "id.csv")
    if not os.path.isdir(project_root_folder) or not os.path.isfile(csv_path):
        return False
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            fid = f.read().strip()
        return fid == str(project_id).strip()
    except OSError:
        return False


def verify_last_session_path(project_id, project_info):
    """上次会话记录在库中的路径是否仍指向同一项目目录。"""
    root = get_project_root_folder(project_info)
    return disk_matches_project(project_id, root)


def maybe_warn_duplicate_project_root_on_open(
    parent, project_id, project_info, selected_folder
) -> bool:
    """
    「双根」检测：库内登记的项目根与当前所选目录不同，但两处均存在合规 id.csv（同一项目编号）。
    此时继续打开仍会加载同一套数据库，易造成本地副本不同步。
    返回 True 表示用户确认继续打开；False 表示取消本次打开。
    """
    if not project_id or not project_info or not selected_folder:
        return True
    sel = os.path.normpath(selected_folder)
    db_root = get_project_root_folder(project_info)
    if not db_root:
        return True
    db_root = os.path.normpath(db_root)
    if sel == db_root:
        return True
    if not (
        disk_matches_project(project_id, sel)
        and disk_matches_project(project_id, db_root)
    ):
        return True
    msg = (
        "该项目在本地有两处重复的项目文件夹：\n\n"
        f"软件登记的项目路径：\n{db_root}\n\n"
        f"您本次选的项目路径：\n{sel}\n\n"
        "为避免数据混乱，本地应保留与软件登记路径一致的项目文件夹，请自行删除另一处。\n\n"
        "是否仍从当前所选目录打开？"
    )
    msgbox = QMessageBox(parent)
    msgbox.setIcon(QMessageBox.Question)
    msgbox.setWindowTitle("重复项目根")
    msgbox.setText(msg)
    msgbox.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    msgbox.setDefaultButton(QMessageBox.No)
    msgbox.button(QMessageBox.Yes).setText("确认")
    msgbox.button(QMessageBox.No).setText("取消")
    return msgbox.exec_() == QMessageBox.Yes


def warn_if_old_project_root_still_valid_after_relocate(
    project_id, project_info, new_root_folder, parent=None
):
    """
    路径迁移成功后：若旧登记根目录仍在磁盘上且 id.csv 仍指向同一项目，提示用户处理旧副本，避免双根。
    """
    if not project_id or not project_info or not new_root_folder:
        return
    old_root = get_project_root_folder(project_info)
    new_root = os.path.normpath(new_root_folder)
    if not old_root:
        return
    old_root = os.path.normpath(old_root)
    if old_root == new_root:
        return
    if disk_matches_project(project_id, old_root):
        w = parent or getattr(bianl, "main_window", None)
        QMessageBox.warning(
            w,
            "请处理旧项目副本",
            "数据库路径已更新到新位置，但旧路径下仍有重复的同一项目文件夹：\n\n"
            f"{old_root}\n\n"
            "请删掉该项目文件夹的id.csv，或移走整个旧文件夹，只保留新路径下的一套，避免数据混淆。",
        )


def _sync_activity_table_paths(project_id, old_folder, new_folder):
    """与 project_confirm_btn 修改项目时一致：批量更新产品设计活动表绝对路径。"""
    old_folder = os.path.normpath(old_folder) if old_folder else ""
    new_folder = os.path.normpath(new_folder)

    conn_act = common_usage.get_mysql_connection_active()
    cursor_act = conn_act.cursor()
    cursor_act.execute(
        "SELECT 产品ID, 产品文件夹绝对路径 FROM 产品设计活动表 WHERE 项目ID = %s",
        (project_id,),
    )
    products = cursor_act.fetchall()
    updated_count = 0
    for product in products:
        if isinstance(product, dict):
            pid = product.get("产品ID")
            old_path = product.get("产品文件夹绝对路径")
        else:
            pid = product[0]
            old_path = product[1]
        if not old_path:
            continue
        try:
            if old_folder and old_folder in old_path:
                new_product_path = old_path.replace(old_folder, new_folder)
            else:
                product_folder_name = os.path.basename(old_path)
                new_product_path = os.path.join(new_folder, product_folder_name)
            new_product_path = os.path.normpath(new_product_path)
            cursor_act.execute(
                "UPDATE 产品设计活动表 SET 产品文件夹绝对路径 = %s WHERE 产品ID = %s",
                (new_product_path, pid),
            )
            updated_count += 1
        except Exception as e:
            print(f"[project_path_relocate] 更新产品路径失败 产品ID={pid}: {e}")
    conn_act.commit()
    cursor_act.close()
    conn_act.close()
    print(f"[project_path_relocate] 已同步 {updated_count} 条产品设计活动表路径")


def relocate_project_paths(project_id, project_info, new_root_folder):
    """
    用户已选择新的项目根目录（内含 id.csv，与 manual 打开项目选中的层级一致）。
    更新 项目需求表.项目保存路径 及 产品设计活动表。
    """
    new_root_folder = os.path.normpath(new_root_folder)
    new_save_path = os.path.dirname(new_root_folder)
    old_folder = get_project_root_folder(project_info)
    # 迁移前快照，用于迁移后检测旧根是否仍为「有效双根」
    project_info_snapshot = dict(project_info) if isinstance(project_info, dict) else project_info

    conn = common_usage.get_mysql_connection_project()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE 项目需求表 SET 项目保存路径 = %s WHERE 项目ID = %s",
        (new_save_path, project_id),
    )
    conn.commit()
    cursor.close()
    conn.close()

    _sync_activity_table_paths(project_id, old_folder, new_root_folder)
    print(
        f"[project_path_relocate] 项目 {project_id} 已更新保存路径为: {new_save_path}"
    )
    warn_if_old_project_root_still_valid_after_relocate(
        project_id, project_info_snapshot, new_root_folder
    )
    return True


def prompt_and_relocate(project_id, project_info):
    """
    弹出说明与文件夹选择，直到用户取消或成功更新数据库。
    返回 True 表示已成功 relocate；False 表示用户取消或始终不匹配。
    """
    parent = getattr(bianl, "main_window", None)
    mb = QMessageBox(parent)
    mb.setIcon(QMessageBox.Warning)
    mb.setWindowTitle("项目文件夹无法访问")
    mb.setText(
        "上次打开的项目在本机原路径下未找到（可能被移动或重命名）。\n\n"
        "请浏览并确认该项目当前所在的新路径。"
    )
    mb.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
    mb.setDefaultButton(QMessageBox.Ok)
    mb.button(QMessageBox.Ok).setText("确认")
    mb.button(QMessageBox.Cancel).setText("取消")
    if mb.exec_() != QMessageBox.Ok:
        return False
    start_dir = ""
    old_root = get_project_root_folder(project_info)
    if old_root:
        p = os.path.dirname(old_root)
        if p and os.path.isdir(p):
            start_dir = p

    while True:
        folder = QFileDialog.getExistingDirectory(
            parent,
            "选择上一次打开的项目根目录（该层须有对应项目的id.csv）",
            start_dir,
        )
        if not folder:
            return False
        folder = os.path.normpath(folder)
        csv_path = os.path.join(folder, "id.csv")
        if not os.path.isfile(csv_path):
            QMessageBox.warning(
                parent,
                "无效文件夹",
                "所选目录并非上一次打开的项目对应的文件夹。请重新选择。\n\n"
                # "请进入资源管理器确认：项目搬家后，包含 id.csv 的那一层文件夹是哪一级，"
                # "请重新选择。",
            )
            continue
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                fid = f.read().strip()
        except OSError as e:
            QMessageBox.warning(parent, "读取失败", f"无法读取 id.csv：{e}")
            continue
        if fid != str(project_id).strip():
            QMessageBox.warning(
                parent,
                "项目不一致",
                "该文件夹内的项目编号与上一次打开的项目编号不一致。请重新选择。\n\n"

            )
            continue
        try:
            relocate_project_paths(project_id, project_info, folder)
            QMessageBox.information(
                parent,
                "路径已更新",
                "已更新数据库中的项目保存路径与产品文件夹路径。",
            )
            return True
        except Exception as e:
            QMessageBox.critical(parent, "更新失败", str(e))
            return False
