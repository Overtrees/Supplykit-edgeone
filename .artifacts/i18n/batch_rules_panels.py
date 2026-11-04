LOCALE = {
    "rpn.c_wh": ("C 仓", "C warehouse"),
    "rpn.b_wh": ("B 仓", "B warehouse"),
    "rpn.supplier_section": ("供应商起订（可选）", "Supplier MOQ (optional)"),
    "rpn.supplier_all": ("通用（所有供应商）", "Generic (all suppliers)"),
    "rp.delete_rule_op": ("删除规则", "Delete rule"),
}

REPL = [
    (
        "frontend/src/pages/RulesPage.tsx",
        """      label: '删除规则',""",
        """      label: t('rp.delete_rule_op'),""",
    ),
]
