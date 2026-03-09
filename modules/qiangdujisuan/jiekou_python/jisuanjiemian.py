import json
import traceback
import pymysql
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QTextEdit, QLabel, QMessageBox
from PyQt5.QtCore import QThread, pyqtSignal, QTimer

from modules.chanpinguanli.chanpinguanli_main import product_manager
from modules.condition_input.view import check_project_and_product
from modules.qiangdujisuan.jiekou_python.combine_json_new import calculate_heat_exchanger_strength as calculate_heat_exchanger_strength_ABEU
from modules.qiangdujisuan.jiekou_python.combine_json_new_abes import calculate_heat_exchanger_strength as calculate_heat_exchanger_strength_ABES

product_id = None

def on_product_id_changed(new_id):
    print(f"Received new PRODUCT_ID: {new_id}")
    global product_id
    product_id = new_id
product_manager.product_id_changed.connect(on_product_id_changed)

class CalculationThread(QThread):
    finished_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)

    def __init__(self, product_id):
        super().__init__()
        self.product_id = product_id

    def run(self):
        try:
            conn = pymysql.connect(
                host="localhost",
                user="root",
                password="123456",
                database="产品设计活动库",
                charset="utf8mb4"
            )
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 产品型式 
                FROM 产品设计活动表 
                WHERE 产品ID = %s
            """, (self.product_id,))
            row = cursor.fetchone()
            conn.close()

            if not row:
                raise ValueError(f"未找到 product_id={self.product_id} 对应的产品型式")

            product_type = row[0]
            print("产品型式为", product_type)

            if product_type in ("AEU", "BEU"):
                result = calculate_heat_exchanger_strength_ABEU(self.product_id)
            elif product_type in ("AES", "BES"):
                result = calculate_heat_exchanger_strength_ABES(self.product_id)
            else:
                raise ValueError(f"未知的产品型式: {product_type}")

            if isinstance(result, str):
                result = json.loads(result)

            simple_result = {
                "Logs": result["Logs"],
                "DictOutDatas": {
                    name: data["IsSuccess"]
                    for name, data in result["DictOutDatas"].items()
                    if isinstance(data, dict) and "IsSuccess" in data
                }
            }

            self.finished_signal.emit(simple_result)

        except Exception:
            self.error_signal.emit(traceback.format_exc())


class JisuanResultViewer(QWidget):
    def __init__(self, line_tip=None, parent=None):
        super().__init__(parent)

        # 0903会议纪要 首先进行项目和产品检查
        print("准备检查项目和产品状态...")
        can_open, msg = check_project_and_product()
        if not can_open:
            QMessageBox.information(self, "提示", msg)
            self.deleteLater()  # 不打开界面
            return  # 立即返回

        self.line_tip = line_tip
        self.setMinimumHeight(400)

        layout = QVBoxLayout(self)
        self.status_label = QLabel("等待计算...", self)
        layout.addWidget(self.status_label)

        self.text_view = QTextEdit(self)
        self.text_view.setReadOnly(True)
        layout.addWidget(self.text_view)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_timeout)
        self.elapsed = 0
        self.is_finished = False  # ✅ 标记计算是否结束

        self.load_result()

    def load_result(self):
        self.text_view.setPlainText("正在计算，请稍后...")
        self.status_label.setText("⏳ 正在计算，请稍后...")
        self.status_label.setStyleSheet("color: blue;")

        # 启动后台线程
        self.thread = CalculationThread(product_id)
        self.thread.finished_signal.connect(self.display_result)
        self.thread.error_signal.connect(self.display_error)
        self.thread.start()

        # 启动计时器
        self.elapsed = 0
        self.is_finished = False
        self.timer.start(1000)

    def check_timeout(self):
        if self.is_finished:
            self.timer.stop()
            return

        self.elapsed += 1
        if self.elapsed == 900:  # 15分钟
            self.status_label.setText("⚠️ 计算时间过长，可能发生异常，请联系相关人员查看数据。")
            self.status_label.setStyleSheet("color: orange;")

    def display_result(self, result):
        """计算成功回调"""
        self.is_finished = True
        self.timer.stop()

        pretty_result = json.dumps(result, ensure_ascii=False, indent=4)
        self.text_view.setPlainText(pretty_result)
        self.status_label.setText("✅ 计算完成")
        self.status_label.setStyleSheet("color: green;")

        has_failure = any(not success for success in result["DictOutDatas"].values())
        if has_failure and self.line_tip:
            self.line_tip.setStyleSheet("color: orange;")
            self.line_tip.setText(
                "计算结果出现不通过的情况，请对照输入输出文件核查：shuru_jisuan.json 与 jisuan_output_new.json\n\n"
            )

    def display_error(self, error_text):
        """异常回调"""
        self.is_finished = True
        self.timer.stop()
        self.text_view.setPlainText(f"发生错误：\n{error_text}")
        self.status_label.setText("❌ 计算失败")
        self.status_label.setStyleSheet("color: red;")
