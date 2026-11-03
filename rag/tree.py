"""Helpers on the plan's node ids that both the build and the retriever use."""


def in_subtree(node_id: str, root: str) -> bool:
    """Whether a node id is `root` or below it: sections 4.4.2, clauses 4.4.2(א), records 5.4[3]."""
    return node_id == root or node_id.startswith((f"{root}.", f"{root}(", f"{root}["))
