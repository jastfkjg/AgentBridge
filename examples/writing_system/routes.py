from fastapi import APIRouter

router = APIRouter()


@router.get("/projects/{project_id}/outline")
def get_outline(project_id: str):
    return {}


@router.post("/projects/{project_id}/chapters/{chapter_id}/scenes")
def create_scene(project_id: str, chapter_id: str, title: str):
    return {}


@router.delete("/projects/{project_id}/chapters/{chapter_id}")
def delete_chapter(project_id: str, chapter_id: str):
    return {}

