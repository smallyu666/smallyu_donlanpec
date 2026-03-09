from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QMessageBox, QLabel, QComboBox
import pymysql
import time
from modules.guankoudingyi.db_cnt import get_connection, db_config_2
from modules.guankoudingyi.funcs.funcs_pipe_table import ensure_hidden_maps, get_next_pipe_id_runtime

# 材料库配置（用于查询模板）
db_config_material = {
    'host': 'localhost',
    'port': 3306,
    'user': 'root',
    'password': '123456',
    'database': '材料库'
}

def save_all_pipe_data(stats_widget):
    """
    保存策略：
    - 对每一行（除最后空行）：
        * 必须有 管口代号
        * 取隐藏 管口ID；若无（极端情况），运行期分配一个
        * 对 产品设计活动表_管口表 做 INSERT ... ON DUPLICATE KEY UPDATE
        * 同步对 产品设计活动表_管口类别表 做 INSERT ... ON DUPLICATE KEY UPDATE（四项：产品ID、管口ID、管口代号、管口所属元件）
    - 对 stats_widget.deleted_pipe_ids ：逐个 DELETE WHERE 产品ID AND 管口ID（同时删除两张表里的对应记录）
    """
    ensure_hidden_maps(stats_widget)
    # 获取表格和产品ID
    table = stats_widget.tableWidget_pipe
    product_id = stats_widget.product_id
    if not product_id:
        QMessageBox.warning(stats_widget, "错误", "产品ID不能为空")
        return
        # ===== 保存前校验：“管口功能”必填（仅校验已填写“管口代号”的行） =====
    if table is not None:
        missing_codes = []
        last_row = table.rowCount() - 1  # 排除最后空行
        for row in range(last_row):
            code_item = table.item(row, 1)  # 管口代号
            func_item = table.item(row, 2)  # 管口功能
            code = code_item.text().strip() if code_item else ""
            func = func_item.text().strip() if func_item else ""
            if code and not func:
                missing_codes.append(code)
        if missing_codes:
            msg = "请输入管口代号为 " + "、".join(missing_codes) + " 的管口功能"
            if hasattr(stats_widget, 'line_tip'):
                stats_widget.line_tip.setText(msg)
                stats_widget.line_tip.setStyleSheet("color: #FFA500;")  # 橘色提示
            return
        # ===== 校验通过，继续原有保存逻辑 =====


    # 定义列映射
    column_map = {
        1: "管口代号",
        2: "管口功能",
        3: "管口用途",
        4: "公称尺寸",
        5: "法兰标准",
        6: "压力等级",
        7: "法兰型式",
        8: "密封面型式",
        9: "焊端规格",
        10: "管口所属元件",
        11: "轴向定位基准",
        12: "轴向定位距离",
        13: "轴向夹角（°）",
        14: "周向方位（°）",
        15: "偏心距",
        16: "外伸高度",
        17: "管口附件",
        18: "管口载荷"
    }

    conn = None
    cur = None
    try:
        conn = get_connection(**db_config_2)
        cur = conn.cursor(pymysql.cursors.DictCursor)

        # —— 1) 先处理延迟删除 ——
        for hid in list(stats_widget.deleted_pipe_ids):
            # 管口表
            cur.execute("""
                DELETE FROM 产品设计活动表_管口表
                WHERE 产品ID=%s AND 管口ID=%s
            """, (product_id, hid))
            # 管口类别表（新增）
            cur.execute("""
                DELETE FROM 产品设计活动表_管口类别表
                WHERE 产品ID=%s AND 管口ID=%s
            """, (product_id, hid))
            #管口载荷表（补充）
            cur.execute("""
                DELETE FROM 产品设计活动表_管口载荷表
                WHERE 产品ID=%s AND 管口ID=%s
            """, (product_id, hid))
        stats_widget.deleted_pipe_ids.clear()

        # —— 2) 逐行 Upsert（新增/修改）——
        last_row = table.rowCount() - 1
        for row in range(last_row):  # 排除最后空行
            code_item = table.item(row, 1)
            port_code = code_item.text().strip() if code_item else ""
            if not port_code:
                continue

            # 收集行数据
            row_data = {}
            for col, field in column_map.items():
                it = table.item(row, col)
                txt = it.text().strip() if it else ""
                if txt != "":
                    row_data[field] = txt

            # 获取/兜底分配 管口ID（运行期分配，确认时才落库）
            hid = stats_widget.row_hidden_pipe_id.get(row)
            if not hid:
                hid = get_next_pipe_id_runtime(stats_widget, product_id)
                stats_widget.row_hidden_pipe_id[row] = hid  # 写回运行期映射

            # —— 2.1 写 "产品设计活动表_管口表"
            row_data.pop("管口代号", None)  # ✅ 删除潜在重复字段
            fields = ["产品ID", "管口ID", "管口代号", "管口更改状态"] + list(row_data.keys())
            values = [product_id, hid, port_code, "已更改"] + list(row_data.values())
            placeholders = ", ".join(["%s"] * len(fields))
            set_clause = ", ".join([f"`{k}`=VALUES(`{k}`)" for k in row_data.keys()] + [
                "`管口代号`=VALUES(`管口代号`)", "`管口更改状态`='已更改'"
            ])

            sql = f"""
                    INSERT INTO 产品设计活动表_管口表 (`{'`, `'.join(fields)}`)
                    VALUES ({placeholders})
                    ON DUPLICATE KEY UPDATE {set_clause}
                """
            cur.execute(sql, values)

            # —— 2.2 同步写 "产品设计活动表_管口类别表"（四列）
            # 获取管口所属元件
            component = row_data.get("管口所属元件", "")
            cur.execute("""
                INSERT INTO 产品设计活动表_管口类别表 (`产品ID`, `管口ID`, `管口代号`, `管口所属元件`)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE `管口代号`=VALUES(`管口代号`), `管口所属元件`=VALUES(`管口所属元件`)
            """, (product_id, hid, port_code, component))

        conn.commit()

        # —— 3) 保存管口附件数据 ——
        save_pipe_attachment_data(product_id, conn, cur)

        # 在line_tip中显示保存成功信息
        if hasattr(stats_widget, 'line_tip'):
            stats_widget.line_tip.setText("保存成功！")
            stats_widget.line_tip.setStyleSheet("color: black;")
            QTimer.singleShot(5000, lambda: stats_widget.line_tip.setText(""))
    except Exception as e:
        if conn:
            conn.rollback()
        # 在line_tip中显示保存失败信息
        if hasattr(stats_widget, 'line_tip'):
            stats_widget.line_tip.setText(f"保存失败：{e}")
            stats_widget.line_tip.setStyleSheet("color: red;")
            QTimer.singleShot(5000, lambda: stats_widget.line_tip.setText(""))
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_type_selections_from_table_header(stats_widget):
    """
    从表头获取类型选择数据
    :param stats_widget: Stats类实例（更改参数类型以便访问comboBox组件）
    :return: 类型选择字典
    """
    type_selections = {}
    
    # ✅ 使用新的组件命名，不再使用findChild方式
    combo_mapping = [
        (stats_widget.combo_nominal_size_type, "公称尺寸类型"),
        (stats_widget.combo_pressure_level_type, "公称压力类型"),
        (stats_widget.combo_weld_end_spec_type, "焊端规格类型")
    ]
    
    for combo, db_field_name in combo_mapping:
        if combo is not None:
            selected_value = combo.currentText()
            type_selections[db_field_name] = selected_value
    
    return type_selections

def save_pipe_type_selection(stats_widget):
    """
    保存选中的公称尺寸类型、公称压力类型、焊端规格类型到数据库
    :param stats_widget: 主窗口实例
    """
    conn = None
    cursor = None
    
    try:
        # 验证产品ID
        product_id = stats_widget.product_id
        if not product_id:
            QMessageBox.warning(stats_widget, "错误", "产品ID不能为空")
            return False

        # 获取类型选择数据
        type_selections = get_type_selections_from_table_header(stats_widget)
        
        # 验证必需字段
        required_fields = ["公称尺寸类型", "公称压力类型", "焊端规格类型"]
        missing_fields = [field for field in required_fields if field not in type_selections]
        if missing_fields:
            QMessageBox.warning(stats_widget, "错误", f"未能获取到以下字段的选择值：{', '.join(missing_fields)}")
            return False

        # 数据库操作
        conn = get_connection(**db_config_2)
        cursor = conn.cursor()

        # 这里使用删除再插入的方式确保数据一致性
        cursor.execute("DELETE FROM 产品设计活动表_管口类型选择表 WHERE 产品ID = %s", (product_id,))
        
        sql = """
            INSERT INTO 产品设计活动表_管口类型选择表 
            (产品ID, 公称尺寸类型, 公称压力类型, 焊端规格类型) 
            VALUES (%s, %s, %s, %s)
        """
        values = (
            product_id,
            type_selections["公称尺寸类型"],
            type_selections["公称压力类型"], 
            type_selections["焊端规格类型"]
        )
        cursor.execute(sql, values)
        conn.commit()
        
        return True

    except Exception as e:
        if conn:
            conn.rollback()
        QMessageBox.critical(stats_widget, "保存失败", f"保存管口类型选择时出错：{str(e)}")
        return False

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def save_all_data_combined(stats_widget):
    """
    保存所有数据的组合方法：先保存管口类型选择，再保存管口数据
    :param stats_widget: 主窗口实例
    """
    # 先保存管口类型选择
    if save_pipe_type_selection(stats_widget):
        # 再保存管口数据
        save_all_pipe_data(stats_widget)

def save_pipe_attachment_data(product_id, conn, cur):
    """
    保存管口附件数据到产品设计活动表_管口附件附加参数表
    1. 从产品设计活动表_管口表统计哪些管口选择了哪些附件类型
    2. 从模板库的管口附件附加参数表获取模板参数结构（模板ID=9）
    3. 根据附件类型分组，写入到产品活动库
    """
    try:
        # 1. 统计哪些管口选择了哪些附件类型
        cur.execute("""
            SELECT 管口代号, 管口附件
            FROM 产品设计活动表_管口表
            WHERE 产品ID = %s AND 管口附件 IS NOT NULL AND 管口附件 != ''
        """, (product_id,))
        pipe_attachments = cur.fetchall()

        if not pipe_attachments:
            print("[管口附件] 没有管口选择附件类型，清理旧数据后退出")
            cur.execute("""
                DELETE FROM 产品设计活动表_管口附件附加参数表
                WHERE 产品ID = %s
            """, (product_id,))
            conn.commit()
            return

        # 按附件类型分组管口号
        # 现在“管口附件”字段支持多选，数据库中以";"分隔，如："接管法兰配对法兰;接管拉筋"
        # 因此需要先按";"拆分，再把同一个管口编号添加到多个附件类型分组里
        attachment_groups = {}
        for row in pipe_attachments:
            pipe_code = row.get('管口代号') if isinstance(row, dict) else row[0]
            raw_attachment = row.get('管口附件') if isinstance(row, dict) else row[1]

            if not raw_attachment:
                continue

            # 解析多选附件，和前端下拉框保持一致，使用 ";" 作为分隔符
            # 同时去掉首尾空格，过滤掉空字符串
            attachment_list = [
                a.strip() for a in str(raw_attachment).split(";") if a.strip()
            ]

            for attachment_type in attachment_list:
                if attachment_type not in attachment_groups:
                    attachment_groups[attachment_type] = []
                attachment_groups[attachment_type].append(pipe_code)

        if not attachment_groups:
            print("[管口附件] 没有有效的附件类型分组，清理旧数据后退出")
            cur.execute("""
                DELETE FROM 产品设计活动表_管口附件附加参数表
                WHERE 产品ID = %s
            """, (product_id,))
            conn.commit()
            return

        # 3. 获取当前产品使用的模板ID
        cur.execute("""
            SELECT 模板名称
            FROM 产品设计活动表_元件材料表
            WHERE 产品ID = %s
            LIMIT 1
        """, (product_id,))
        template_result = cur.fetchone()
        if not template_result:
            print("[管口附件] 未找到产品的模板名称，跳过保存")
            return

        template_name = template_result.get('模板名称') if isinstance(template_result, dict) else template_result[0]
        if not template_name:
            print("[管口附件] 产品模板名称为空，跳过保存")
            return

        # 4. 从材料库的元件材料模板表获取模板ID
        conn_material = pymysql.connect(**db_config_material)
        try:
            cur_material = conn_material.cursor(pymysql.cursors.DictCursor)

            # 根据模板名称从元件材料模板表查询模板ID
            cur_material.execute("""
                SELECT 模板ID
                FROM 元件材料模板表
                WHERE 模板名称 = %s
                LIMIT 1
            """, (template_name,))
            template_id_result = cur_material.fetchone()
            if not template_id_result:
                print(f"[管口附件] 材料库中没有找到模板名称 '{template_name}' 的模板ID")
                return

            template_id = template_id_result.get('模板ID') if isinstance(template_id_result, dict) else template_id_result[0]

            # 5. 查询模板参数结构（包含附件类型字段）
            cur_material.execute("""
                SELECT Tab分类, 附件类型, 标题分组, 参数名称, 参数数值, 参数单位
                FROM 管口附件附加参数表
                WHERE 模板ID = %s
                ORDER BY Tab分类, 标题分组, 参数ID
            """, (template_id,))
            template_params = cur_material.fetchall()

            if not template_params:
                print(f"[管口附件] 模板库中没有找到模板ID={template_id}的参数结构")
                return

            # 6. 先删除该产品现有的管口附件数据
            cur.execute("""
                DELETE FROM 产品设计活动表_管口附件附加参数表
                WHERE 产品ID = %s
            """, (product_id,))

            # 7. 获取当前最大的参数ID，用于生成新的参数ID
            cur.execute("""
                SELECT COALESCE(MAX(参数ID), 0) as max_id
                FROM 产品设计活动表_管口附件附加参数表
            """)
            max_id_result = cur.fetchone()
            max_param_id = max_id_result.get('max_id') if isinstance(max_id_result, dict) else (max_id_result[0] if max_id_result else 0)
            next_param_id = max_param_id + 1

            # 8. 根据附件类型分组，写入数据
            insert_count = 0
            # 为每个附件类型生成唯一的 tab_id（使用时间戳，确保唯一性和顺序）
            base_timestamp = int(time.time() * 1000)  # 毫秒级时间戳
            tab_id_counter = 0

            for attachment_type, pipe_codes in attachment_groups.items():
                # 为每个 Tab分类 生成唯一的 tab_id
                # 使用基础时间戳 + 计数器，确保每个 tab 有唯一的 ID
                tab_id = base_timestamp + tab_id_counter
                tab_id_counter += 1

                print(f"[管口附件] 为 Tab分类 '{attachment_type}' 生成 tab_id: {tab_id}")

                # 筛选出该附件类型的模板参数
                type_params = [p for p in template_params if p.get('Tab分类') == attachment_type]

                if not type_params:
                    print(f"[管口附件] 附件类型 '{attachment_type}' 在模板中没有找到参数结构")
                    continue

                # 合并管口号：将所有管口号合并为 "N1、N2、N3" 格式
                pipe_codes_str = '、'.join(pipe_codes)

                # 按标题分组组织参数，避免重复插入相同的参数
                # 对于"管口号"参数，只插入一次，值为合并后的管口号
                # 对于其他参数，每个标题分组下的参数都要插入
                for param in type_params:
                    param_name = param.get('参数名称')
                    title_group = param.get('标题分组', '')
                    attachment_type_from_template = param.get('附件类型', '')  # 从模板表中获取附件类型

                    # 对于"管口号"参数，写入合并后的管口号值
                    if param_name == '管口号':
                        param_value = pipe_codes_str
                    else:
                        param_value = param.get('参数数值', '')

                    cur.execute("""
                        INSERT INTO 产品设计活动表_管口附件附加参数表
                        (参数ID, 产品ID, Tab分类, 附件类型, 标题分组, 参数名称, 参数数值, 参数单位, 模板名称, 模板ID, Tab_ID)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        next_param_id,  # 参数ID
                        product_id,
                        attachment_type,  # Tab分类 = 用户在管口定义中选择的附件类型（如"接管法兰配对法兰"）
                        attachment_type_from_template,  # 附件类型 = 模板表中的附件类型字段值
                        title_group,  # 标题分组 = 模板中的标题分组（如"接管法兰配对法兰"、"接管法兰垫片"等）
                        param_name,
                        param_value,
                        param.get('参数单位'),
                        template_name,  # 模板名称
                        template_id,  # 模板ID
                        tab_id  # Tab_ID = 用于标识tab标签页的ID
                    ))
                    next_param_id += 1
                    insert_count += 1

            print(f"[管口附件] 成功保存 {insert_count} 条参数记录")

            # 提交事务（使用主连接）
            conn.commit()
            print(f"[管口附件] 事务已提交")

        finally:
            conn_material.close()

    except Exception as e:
        print(f"[管口附件] 保存失败: {e}")
        import traceback
        traceback.print_exc()
        # 回滚事务（使用主连接）
        if conn:
            conn.rollback()
            print(f"[管口附件] 事务已回滚")
        # 不抛出异常，避免影响主保存流程


def connect_save_button(stats_widget):
    """
    连接确认按钮的点击事件
    :param stats_widget: 主窗口实例
    """
    stats_widget.pushButton_affirm.clicked.connect(lambda: save_all_data_combined(stats_widget))
