"""产品预定义的只读显示列，保留既有设计阶段/版次的逻辑列号。"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QTableWidgetItem, QStyledItemDelegate

from modules.yudingyi.product_config import get_product_configs


PREDEFINED_COLUMN = 6


class _ReadOnlyDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        return None


def install_predefined_column(table):
    table.setColumnCount(7)
    table.setHorizontalHeaderItem(PREDEFINED_COLUMN, QTableWidgetItem("预定义配置"))
    header = table.horizontalHeader()
    header.moveSection(header.visualIndex(PREDEFINED_COLUMN), header.visualIndex(4))
    table.setItemDelegateForColumn(PREDEFINED_COLUMN, _ReadOnlyDelegate(table))


def refresh_predefined_row(table, row, product_id):
    if table.columnCount() <= PREDEFINED_COLUMN:
        return
    text = ""
    error = ""
    if product_id:
        try:
            text = get_product_configs([product_id]).get(product_id, "") or ""
        except Exception as exc:
            # 保留产品页可用性，但明确显示配置读取失败，计算入口仍会阻止错误配置。
            text = "配置读取失败"
            error = str(exc)
    item = QTableWidgetItem(text)
    item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
    item.setToolTip(error or text)
    blocked = table.blockSignals(True)
    try:
        table.setItem(row, PREDEFINED_COLUMN, item)
    finally:
        table.blockSignals(blocked)
