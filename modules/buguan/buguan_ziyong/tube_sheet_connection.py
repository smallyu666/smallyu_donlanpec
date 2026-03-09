#10/19 3
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame,
    QLineEdit, QGridLayout, QMessageBox, QScrollBar
)
from PyQt5.QtGui import QPixmap
from PyQt5.QtCore import Qt, QSize
import pymysql
from pathlib import Path


def create_component_connection():
    """创建元件库数据库连接"""
    try:
        return pymysql.connect(
            host='localhost',
            port=3306,
            database='元件库',
            user='root',
            password='123456',
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
    except pymysql.MySQLError as e:
        QMessageBox.critical(None, "数据库错误", f"连接元件库失败: {e}")
        return None


def create_product_connection():
    """创建产品设计活动库数据库连接"""
    try:
        return pymysql.connect(
            host='localhost',
            database='产品设计活动库',
            user='root',
            password='123456',
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
    except pymysql.MySQLError as e:
        QMessageBox.critical(None, "数据库错误", f"连接产品设计活动库失败: {e}")
        return None


class TubeSheetConnectionPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.current_params = []
        self.current_image_path = ""
        self.current_dir = Path(__file__).parent.resolve()
        self.connection_types = [
            "强度焊接加贴胀管孔结构",
            "机械胀接管孔结构",
            "强度焊接的焊缝形式",
            "机械强度胀接加密封焊管孔结构",
            "内孔焊接头形式"
        ]
        self.image_labels = []
        self.thumbnail_size = QSize(320, 220)
        self.setup_ui()

    def get_product_id(self):
        try:
            if hasattr(self.parent, 'productID'):
                pid = getattr(self.parent, 'productID')
                return pid if pid else None
            return None
        except Exception:
            return None

    def setup_ui(self):
        """主布局"""
        outer_layout = QHBoxLayout(self)
        outer_layout.setContentsMargins(20, 20, 20, 20)
        outer_layout.setSpacing(30)

        # ---------------- 左侧图片区 + 滚动条 ----------------
        left_outer = QVBoxLayout()
        left_outer.setSpacing(5)
        left_frame = QFrame()
        left_frame.setStyleSheet("QFrame { background-color: #ffffff; border-radius: 6px; }")
        left_layout = QVBoxLayout(left_frame)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(12)

        self.image_scroll = QScrollArea()
        self.image_scroll.setWidgetResizable(True)
        self.image_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.image_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)  # ✅ 启用水平滚动
        self.image_scroll.setStyleSheet("background-color: #ffffff;")

        self.image_container = QWidget()
        self.image_container_layout = QVBoxLayout(self.image_container)
        self.image_container_layout.setContentsMargins(6, 6, 6, 6)
        self.image_container_layout.setSpacing(18)

        # 添加图片内容
        for conn_type in self.connection_types:
            title = QLabel(conn_type)
            title.setStyleSheet("font-size:18px; font-weight:600; color:#222;")
            self.image_container_layout.addWidget(title)

            grid_frame = QFrame()
            grid_layout = QGridLayout(grid_frame)
            grid_layout.setSpacing(14)
            grid_layout.setContentsMargins(0, 0, 0, 0)
            grid_layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)

            folder = self.current_dir.joinpath("static", conn_type)
            images = []
            if folder.exists() and folder.is_dir():
                images = sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".png"])

            for idx, img_path in enumerate(images):
                lbl = QLabel()
                lbl.setFixedSize(self.thumbnail_size)
                lbl.setAlignment(Qt.AlignCenter)
                lbl.setStyleSheet("""
                    QLabel {
                        border: 2px solid #ddd;
                        border-radius: 6px;
                        background-color: white;
                    }
                    QLabel:hover {
                        border: 2px solid #4CAF50;
                    }
                    QLabel[selected="true"] {
                        border: 3px solid #2196F3;
                    }
                """)
                try:
                    pix = QPixmap(str(img_path))
                    if not pix.isNull():
                        scaled = pix.scaled(
                            self.thumbnail_size.width()-10,
                            self.thumbnail_size.height()-10,
                            Qt.KeepAspectRatio,
                            Qt.SmoothTransformation
                        )
                        lbl.setPixmap(scaled)
                except Exception as e:
                    print(f"[图片加载错误] {img_path}: {e}")

                lbl.image_path = str(img_path)
                lbl.tube_sheet_type = self.infer_tube_sheet_type(img_path.stem)
                lbl.connection_type = conn_type
                lbl.setProperty("selected", False)
                lbl.mousePressEvent = self._make_label_click_handler(lbl)
                self.image_labels.append(lbl)
                grid_layout.addWidget(lbl, idx // 4, idx % 4)

            self.image_container_layout.addWidget(grid_frame)

        self.image_container_layout.addStretch()
        self.image_scroll.setWidget(self.image_container)
        left_layout.addWidget(self.image_scroll)

        # ✅ 下方水平滚动条
        self.left_hscroll = QScrollBar(Qt.Horizontal)
        self.image_scroll.setHorizontalScrollBar(self.left_hscroll)
        left_outer.addWidget(left_frame)
        left_outer.addWidget(self.left_hscroll)

        outer_layout.addLayout(left_outer, 3)

        # ---------------- 右侧参数区 + 滚动条 ----------------
        right_outer = QVBoxLayout()
        right_outer.setSpacing(5)
        self.param_frame = QFrame()
        self.param_frame.setStyleSheet("""
            QFrame {
                background-color: #f9f9f9;
                border-radius: 8px;
            }
        """)
        self.param_layout = QVBoxLayout(self.param_frame)
        self.param_layout.setContentsMargins(15, 15, 15, 15)
        self.param_layout.setSpacing(15)

        param_title = QLabel("参数设置")
        param_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #333;")
        param_title.setAlignment(Qt.AlignCenter)
        self.param_layout.addWidget(param_title)

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        separator.setStyleSheet("color: #ddd;")
        self.param_layout.addWidget(separator)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)  # ✅ 启用水平滚动

        self.scroll_content = QWidget()
        self.scroll_param_layout = QVBoxLayout(self.scroll_content)
        self.scroll_param_layout.setContentsMargins(10, 10, 10, 10)
        self.scroll_param_layout.setSpacing(20)

        self.scroll_area.setWidget(self.scroll_content)
        self.param_layout.addWidget(self.scroll_area)

        # ✅ 下方水平滚动条
        self.right_hscroll = QScrollBar(Qt.Horizontal)
        self.scroll_area.setHorizontalScrollBar(self.right_hscroll)
        # ✅ 新增：右侧垂直滚动条
        self.right_vscroll = QScrollBar(Qt.Vertical)
        self.scroll_area.setVerticalScrollBar(self.right_vscroll)


        right_outer.addWidget(self.param_frame)
        right_outer.addWidget(self.right_hscroll)

        outer_layout.addLayout(right_outer, 2)

        # ✅ 统一滚动条样式（灰色风格）
        scrollbar_style = """
        QScrollBar:horizontal, QScrollBar:vertical {
            border: none;
            background: #f0f0f0;
            height: 12px;
            width: 12px;
            margin: 0px;
            border-radius: 6px;
        }
        QScrollBar::handle:horizontal, QScrollBar::handle:vertical {
            background: #b0b0b0;
            border-radius: 6px;
            min-width: 20px;
            min-height: 20px;
        }
        QScrollBar::handle:horizontal:hover, QScrollBar::handle:vertical:hover {
            background: #909090;
        }
        QScrollBar::add-line, QScrollBar::sub-line {
            background: none;
            border: none;
        }
        """
        self.image_scroll.verticalScrollBar().setStyleSheet(scrollbar_style)
        self.image_scroll.horizontalScrollBar().setStyleSheet(scrollbar_style)
        self.scroll_area.verticalScrollBar().setStyleSheet(scrollbar_style)
        self.scroll_area.horizontalScrollBar().setStyleSheet(scrollbar_style)
        self.left_hscroll.setStyleSheet(scrollbar_style)
        self.right_hscroll.setStyleSheet(scrollbar_style)
        self.right_vscroll.setStyleSheet(scrollbar_style)


    def _make_label_click_handler(self, lbl):
        def handler(event):
            if event is None or event.button() == Qt.LeftButton:
                self.select_image(lbl)
        return handler

    def infer_tube_sheet_type(self, filename):
        f = filename.lower()
        if '复合' in f or '0' in f:
            return '0'
        elif '整体' in f or '1' in f:
            return '1'
        elif 'a' in f:
            return 'a'
        elif 'b' in f:
            return 'b'
        elif 'c' in f:
            return 'c'
        elif 'd' in f:
            return 'd'
        else:
            return filename

    def select_image(self, label):
        if not hasattr(label, 'connection_type') or not hasattr(label, 'tube_sheet_type'):
            return

        for lbl in self.image_labels:
            lbl.setProperty("selected", False)
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)
        label.setProperty("selected", True)
        label.style().unpolish(label)
        label.style().polish(label)

        self.current_image_path = getattr(label, 'image_path', '')
        self.clear_parameters()

        conn_type = label.connection_type
        tube_type = label.tube_sheet_type
        params = self.get_parameters_by_type(conn_type, tube_type)

        for param in params:
            param_group = QHBoxLayout()
            param_group.setSpacing(15)
            param_group.setContentsMargins(0, 0, 0, 0)

            name_label = QLabel(f"{param['name']}:")
            name_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #333;")
            name_label.setFixedWidth(220)
            name_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

            input_edit = QLineEdit(param['value'])
            input_edit.setStyleSheet("""
                QLineEdit {
                    font-size: 16px;
                    padding: 8px 12px;
                    border: 2px solid #ccc;
                    border-radius: 6px;
                    background-color: white;
                    min-height: 40px;
                }
                QLineEdit:focus {
                    border: 2px solid #2196F3;
                }
            """)
            input_edit.setFixedWidth(120)
            input_edit.setFixedHeight(40)
            input_edit.textChanged.connect(lambda text, name=param['name']: self.update_param_value(name, text))

            self.current_params.append((param['name'], param['value']))

            container = QWidget()
            container.setFixedHeight(50)
            container.setLayout(param_group)
            param_group.addWidget(name_label)
            param_group.addWidget(input_edit)
            param_group.addStretch()
            self.scroll_param_layout.addWidget(container)

        self.scroll_area.verticalScrollBar().setValue(0)

    def clear_parameters(self):
        for i in reversed(range(self.scroll_param_layout.count())):
            w = self.scroll_param_layout.itemAt(i).widget()
            if w:
                w.deleteLater()
        self.current_params = []

    def update_param_value(self, param_name, param_value):
        for i, (n, v) in enumerate(self.current_params):
            if n == param_name:
                self.current_params[i] = (n, param_value)
                return
        self.current_params.append((param_name, param_value))

    def get_parameters_by_type(self, connection_type, tube_sheet_type):
        product_id = self.get_product_id()
        if product_id:
            prod_conn = create_product_connection()
            if prod_conn:
                try:
                    with prod_conn.cursor() as cur:
                        sql = """
                        SELECT 参数名, 参数值
                        FROM 产品设计活动表_管板连接表
                        WHERE 产品ID = %s AND 管板连接方式 = %s AND 管板类型 = %s
                        """
                        cur.execute(sql, (product_id, connection_type, tube_sheet_type))
                        rows = cur.fetchall()
                        if rows:
                            return [{"name": r["参数名"], "value": r["参数值"]} for r in rows]
                except pymysql.Error as e:
                    print(f"[产品库查询错误] {e}")
                finally:
                    prod_conn.close()

        comp_conn = create_component_connection()
        if not comp_conn:
            return []
        try:
            with comp_conn.cursor() as cur:
                sql = """
                SELECT 参数名, 参数值
                FROM 管板连接表
                WHERE 管板连接方式 = %s AND 管板类型 = %s
                """
                cur.execute(sql, (connection_type, tube_sheet_type))
                rows = cur.fetchall()
                return [{"name": r["参数名"], "value": r["参数值"]} for r in rows]
        except pymysql.Error as e:
            QMessageBox.critical(self, "数据库错误", f"查询失败: {e}")
            return []
        finally:
            comp_conn.close()
