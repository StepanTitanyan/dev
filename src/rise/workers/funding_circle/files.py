import base64
import binascii
import shutil
import uuid
from pathlib import Path
from rise.config.config import settings


def ensure_processing_root():
    root = Path(settings.PROCESSING_FILES_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def create_tracking_folder(tracking_id: str):
    root = ensure_processing_root()
    folder = root / tracking_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def get_or_create_application_folder(application_id: str) -> str:
    """
    Returns the path to the processing folder for a given FC application_id,
    creating it if it does not exist. Used when no folder was created at API
    ingestion time (i.e. no files were attached), so the worker creates it
    when it needs to save downloaded files.
    """
    root = ensure_processing_root()
    folder = root / application_id
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder)


def _safe_pdf_name(filename: str):
    cleaned = Path(filename or "").name.strip()
    if not cleaned:
        raise ValueError("filename is required")
    if not cleaned.lower().endswith(".pdf"):
        raise ValueError("Only PDF files are supported")
    return cleaned


def save_uploaded_documents(tracking_id: str, uploaded_documents: list[dict]):
    folder = create_tracking_folder(tracking_id)
    saved_documents = []

    for index, document in enumerate(uploaded_documents):
        filename = _safe_pdf_name(document.get("filename") or f"document_{index + 1}.pdf")
        content_base64 = document.get("content_base64")
        if not content_base64:
            raise ValueError(f"content_base64 is required for {filename}")

        try:
            file_bytes = base64.b64decode(content_base64, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError(f"Invalid base64 content for {filename}")

        if not file_bytes.startswith(b"%PDF"):
            raise ValueError(f"{filename} is not a valid PDF file")

        file_path = folder / filename
        file_path.write_bytes(file_bytes)

        saved_documents.append({
            "id": str(uuid.uuid4()),
            "filename": filename,
            "document_type": document.get("document_type") or "bank_statement",
            "local_path": str(file_path)})

    return {
        "folder_path": str(folder),
        "documents": saved_documents}


def save_downloaded_documents(folder_path: str, documents: list[dict]) -> list[dict]:
    """
    Saves pre-fetched file bytes into the processing folder.
    Used by the worker after downloading ContentVersion files from Salesforce.

    Each document dict must contain:
        - filename (str): the target filename (must end in .pdf)
        - bytes (bytes): raw file bytes
        - document_type (str, optional): defaults to "bank_statement"

    Returns a list of saved document records.
    """
    folder = Path(folder_path)
    folder.mkdir(parents=True, exist_ok=True)
    saved_documents = []

    for index, document in enumerate(documents):
        filename = _safe_pdf_name(document.get("filename") or f"document_{index + 1}.pdf")
        file_bytes = document.get("bytes")

        if not file_bytes:
            raise ValueError(f"No bytes provided for {filename}")

        if not file_bytes.startswith(b"%PDF"):
            raise ValueError(f"{filename} is not a valid PDF file")

        file_path = folder / filename
        file_path.write_bytes(file_bytes)

        saved_documents.append({
            "id": str(uuid.uuid4()),
            "filename": filename,
            "document_type": document.get("document_type") or "bank_statement",
            "local_path": str(file_path)})

    return saved_documents


def rename_processing_folder(old_folder_name: str, new_folder_name: str):
    root = ensure_processing_root()

    old_path = root / old_folder_name
    new_path = root / new_folder_name

    if not old_path.exists():
        return str(new_path)

    if old_path == new_path:
        return str(new_path)

    if new_path.exists():
        for item in old_path.iterdir():
            destination = new_path / item.name
            if destination.exists():
                destination.unlink()
            shutil.move(str(item), str(destination))
        old_path.rmdir()
        return str(new_path)

    shutil.move(str(old_path), str(new_path))
    return str(new_path)


def list_documents_by_type(folder_path: str | None, document_type: str) -> list[str]:
    """
    Returns sorted paths of all PDF files in folder_path.

    Note: all files stored in a processing folder are currently of the same
    document_type (bank_statement), so type-based filtering is not needed in
    practice. If multiple document types are stored together in future, this
    function should be updated to use a manifest or subfolder convention.
    """
    if not folder_path:
        return []

    folder = Path(folder_path)
    if not folder.exists():
        return []

    return [str(p) for p in sorted(folder.glob("*.pdf"))]