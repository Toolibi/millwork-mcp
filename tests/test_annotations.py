import asyncio

from millwork_mcp.server import mcp

CHANGES_STOCK = {"reserve_job_materials", "consume_job_materials", "release_job_materials",
                 "receive_stock", "count_stock", "log_remnant"}
WRITES_FILES = {"estimate_and_export"}


def test_every_tool_declares_hints():
    tools = asyncio.run(mcp.list_tools())
    assert len(tools) == 17
    for t in tools:
        a = t.annotations
        assert a is not None and a.title, t.name
        assert a.openWorldHint is False, t.name
        if t.name in CHANGES_STOCK | WRITES_FILES:
            assert a.readOnlyHint is False, t.name
        else:
            assert a.readOnlyHint is True and a.destructiveHint is False, t.name
    destructive = {t.name for t in tools if t.annotations.destructiveHint}
    assert destructive == {"consume_job_materials", "release_job_materials",
                           "count_stock", "log_remnant"}
