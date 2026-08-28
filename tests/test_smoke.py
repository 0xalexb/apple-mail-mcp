def test_import_server():
    from apple_mail_mcp import server

    assert hasattr(server, "mcp")
    assert hasattr(server, "main")


def test_ping_tool():
    from apple_mail_mcp.server import ping

    assert ping() == "pong"


def test_flag_colors_tool():
    from apple_mail_mcp.server import flag_colors

    assert flag_colors()[0] == "red"
    assert len(flag_colors()) == 7
