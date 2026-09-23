from . import test_ask_ai_flows
try:
    import odoo  # noqa: F401
except ImportError:
    pass
else:
    from . import test_ask_ai_security
