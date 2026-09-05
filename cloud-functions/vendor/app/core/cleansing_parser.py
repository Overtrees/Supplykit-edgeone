"""文件解析 + 字段清洗 — 从 cleansing.py 拆出"""
import csv, io, re
from openpyxl import load_workbook


def parse_file(content: bytes, filename: str):
    """解析上传文件，返回 dict 列表（DictReader 格式）"""
    if filename.lower().endswith('.csv'):
        text = content.decode('utf-8-sig', errors='ignore')
        return list(csv.DictReader(io.StringIO(text)))
    wb = load_workbook(io.BytesIO(content), data_only=True)
    ws = wb[wb.sheetnames[0]]
    raw = list(ws.iter_rows(values_only=True))
    if not raw:
        return []
    headers = [str(c).strip() if c is not None else '' for c in raw[0]]
    return [{headers[i]: raw[r][i] for i in range(len(headers))} for r in range(1, len(raw))]


def cleanse_value(raw_val, cfg):
    """根据配置清洗单个字段值"""
    if raw_val is None or str(raw_val).strip() == '':
        return cfg.get('default', '')
    v = str(raw_val).strip()
    field_type = cfg.get('type', 'string')
    fmt_str = cfg.get('format', '')
    try:
        if field_type == 'number':
            cleaned = re.sub(r'[^\d.\-]', '', v)
            return float(cleaned) if '.' in cleaned else int(float(cleaned))
        elif field_type == 'date':
            return v[:10] if fmt_str == 'YMD' else v
        else:
            return v
    except Exception as e:
        import logging; logging.warning(f"[clean] parse value {v} error: {e}")
        return v