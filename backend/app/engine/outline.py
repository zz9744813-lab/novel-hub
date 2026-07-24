"""Outline system - §4 v7.3. DAG, dependency validation, version freeze."""
import uuid
from collections import defaultdict, deque
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import OutlineVersion, OutlineNode, OutlineDependency, Chapter


async def validate_dag(db: AsyncSession, book_id: uuid.UUID,
                        outline_version_id: uuid.UUID) -> tuple[bool, list[str]]:
    """§4.3: Validate outline DAG before APPROVED.

    Checks:
    1. Nodes exist
    2. All depends_on references point to valid nodes (UUID or chapter-number style)
    3. Dependency direction: dependee chapter_no < current chapter_no
    4. No cycles (full topological sort)
    """
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

    node_ids = {str(n.id) for n in nodes}
    node_map = {str(n.id): n for n in nodes}
    # Build chapter_no -> UUID map for resolving "ch1" style references
    chapter_to_uuid = {n.chapter_no: str(n.id) for n in nodes}

    def resolve_dep_id(dep_id_raw):
        """Resolve a dependency reference to a UUID string.
        Handles UUIDs, 'ch1', 'ch_1', '1', or integer chapter numbers.
        Returns the UUID string if found, None if unresolvable.
        """
        dep_id_str = str(dep_id_raw) if dep_id_raw is not None else ""

        # If it looks like a UUID, use it directly
        if len(dep_id_str) > 10:
            try:
                uuid.UUID(dep_id_str)
                return dep_id_str
            except ValueError:
                pass

        # Try to parse chapter number from "ch1", "ch_1", "1", or integer
        dep_ch_no = None
        if isinstance(dep_id_raw, int):
            dep_ch_no = dep_id_raw
        elif isinstance(dep_id_raw, str):
            cleaned = dep_id_raw.lower().replace("chapter_", "").replace("chapter", "").replace("ch_", "").replace("ch", "").strip()
            try:
                dep_ch_no = int(cleaned)
            except ValueError:
                pass

        if dep_ch_no is not None and dep_ch_no in chapter_to_uuid:
            return chapter_to_uuid[dep_ch_no]
        return None

    # Build adjacency list for cycle detection
    adj = defaultdict(list)  # node_id -> list of dependency node_ids
    in_degree = defaultdict(int)  # node_id -> count of incoming edges

    for node in nodes:
        nid = str(node.id)
        in_degree.setdefault(nid, 0)
        for dep in node.depends_on or []:
            dep_id_raw = dep.get("node_id", "")
            resolved_id = resolve_dep_id(dep_id_raw)

            if resolved_id is None:
                errors.append(
                    f"Chapter {node.chapter_no}: dependency '{dep_id_raw}' not found in version"
                )
                continue

            # Check reference exists
            if resolved_id not in node_ids:
                errors.append(
                    f"Chapter {node.chapter_no}: dependency {resolved_id} not found in version"
                )
                continue

            # Check direction
            dep_node = node_map.get(resolved_id)
            if dep_node and dep_node.chapter_no >= node.chapter_no:
                errors.append(
                    f"Chapter {node.chapter_no}: depends on future chapter {dep_node.chapter_no}"
                )

            # Build edge for cycle detection: dep -> node (dep must come first)
            adj[resolved_id].append(nid)
            in_degree[nid] += 1

    # Kahn's algorithm for cycle detection (topological sort)
    if not errors:
        queue = deque()
        for nid in node_ids:
            if in_degree[nid] == 0:
                queue.append(nid)

        sorted_count = 0
        while queue:
            current = queue.popleft()
            sorted_count += 1
            for neighbor in adj[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if sorted_count != len(node_ids):
            # Cycle detected
            cyclic_nodes = [nid for nid in node_ids if in_degree[nid] > 0]
            cyclic_chapters = [node_map[nid].chapter_no for nid in cyclic_nodes if nid in node_map]
            errors.append(
                f"Cycle detected in DAG. Involved chapters: {sorted(cyclic_chapters)}"
            )

    return len(errors) == 0, errors


async def check_required_dependencies(db: AsyncSession, book_id: uuid.UUID,
                                      chapter_no: int, outline_version_id: uuid.UUID) -> tuple[bool, list[str]]:
    """Check if all required dependencies for a chapter are satisfied.

    Per §4.4: required dependencies must have their target chapters finalized
    and required_state conditions met.
    """
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

    # Build chapter_no -> node map for resolving chapter references
    all_nodes_result = await db.execute(
        select(OutlineNode).where(
            OutlineNode.outline_version_id == outline_version_id,
        )
    )
    all_nodes = all_nodes_result.scalars().all()
    chapter_to_node = {n.chapter_no: n for n in all_nodes}

    for dep in node.depends_on or []:
        if not dep.get("required"):
            continue

        dep_node_id_raw = dep.get("node_id", "")
        required_state = dep.get("required_state", "")

        # Try UUID lookup first, then chapter-number resolution
        dep_node = None
        if isinstance(dep_node_id_raw, str) and len(dep_node_id_raw) > 10:
            try:
                dep_node_result = await db.execute(
                    select(OutlineNode).where(OutlineNode.id == uuid.UUID(dep_node_id_raw))
                )
                dep_node = dep_node_result.scalar_one_or_none()
            except ValueError:
                pass

        # If UUID lookup failed, try chapter-number resolution
        if dep_node is None:
            dep_ch_no = None
            if isinstance(dep_node_id_raw, int):
                dep_ch_no = dep_node_id_raw
            elif isinstance(dep_node_id_raw, str):
                cleaned = dep_node_id_raw.lower().replace("chapter_", "").replace("chapter", "").replace("ch_", "").replace("ch", "").strip()
                try:
                    dep_ch_no = int(cleaned)
                except ValueError:
                    pass
            if dep_ch_no is not None and dep_ch_no in chapter_to_node:
                dep_node = chapter_to_node[dep_ch_no]

        if dep_node:
            # Check if the dependency chapter is finalized
            ch_result = await db.execute(
                select(Chapter).where(
                    Chapter.book_id == book_id,
                    Chapter.chapter_no == dep_node.chapter_no,
                )
            )
            dep_chapter = ch_result.scalar_one_or_none()

            if not dep_chapter or dep_chapter.status != "finalized":
                unmet.append(
                    f"Chapter {chapter_no} requires chapter {dep_node.chapter_no} to be finalized "
                    f"(current: {dep_chapter.status if dep_chapter else 'not started'})"
                )

        # Validate required_state value
        if required_state and required_state not in ("planted", "resolved", "open", "established"):
            unmet.append(f"Unknown required_state '{required_state}' for chapter {chapter_no}")

    return len(unmet) == 0, unmet
