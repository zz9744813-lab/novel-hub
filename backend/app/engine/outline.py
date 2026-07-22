"""Outline system - §4 v7.3. DAG, dependency validation, version freeze."""
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import OutlineVersion, OutlineNode, OutlineDependency


async def validate_dag(db: AsyncSession, book_id: uuid.UUID,
                        outline_version_id: uuid.UUID) -> tuple[bool, list[str]]:
    """§4.3: Validate outline DAG before APPROVED."""
    errors = []

    # Get all nodes for this version
    result = await db.execute(
        select(OutlineNode).where(
            OutlineNode.book_id == book_id,
            OutlineNode.outline_version_id == outline_version_id,
        ).order_by(OutlineNode.chapter_no)
    )
    nodes = result.scalars().all()

    if not nodes:
        errors.append("No nodes found")
        return False, errors

    node_ids = {n.id for n in nodes}
    node_map = {n.id: n for n in nodes}

    # Check depends_on references
    for node in nodes:
        for dep in node.depends_on or []:
            dep_id = uuid.UUID(dep["node_id"]) if isinstance(dep["node_id"], str) else dep["node_id"]
            if dep_id not in node_ids:
                errors.append(f"Node chapter {node.chapter_no}: dependency {dep_id} not found")

            # Check direction: dependee chapter_no < current chapter_no
            dep_node = node_map.get(dep_id)
            if dep_node and dep_node.chapter_no >= node.chapter_no:
                errors.append(f"Node chapter {node.chapter_no}: depends on future chapter {dep_node.chapter_no}")

    # Check for cycles (simplified - topological sort)
    # TODO: implement full cycle detection

    return len(errors) == 0, errors


async def check_required_dependencies(db: AsyncSession, book_id: uuid.UUID,
                                      chapter_no: int, outline_version_id: uuid.UUID) -> tuple[bool, list[str]]:
    """Check if all required dependencies for a chapter are satisfied."""
    result = await db.execute(
        select(OutlineNode).where(
            OutlineNode.book_id == book_id,
            OutlineNode.outline_version_id == outline_version_id,
            OutlineNode.chapter_no == chapter_no,
        )
    )
    node = result.scalar_one_or_none()
    if not node:
        return False, [f"Chapter {chapter_no} not found in outline"]

    unmet = []
    for dep in node.depends_on or []:
        if dep.get("required"):
            required_state = dep.get("required_state")
            if required_state and required_state not in ("planted", "resolved", "open"):
                unmet.append(f"Required state '{required_state}' not satisfied")

    return len(unmet) == 0, unmet
