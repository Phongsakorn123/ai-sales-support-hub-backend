from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentRead(BaseModel):
    """Document metadata. The storage path is deliberately not exposed."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(examples=["DOC-0001"])
    product_id: int
    title: str
    document_type: str = Field(examples=["datasheet"])
    description: str | None
    file_format: str
    version: str
    approval_status: str = Field(
        description="`mock_demo` for generated demo documents."
    )
    is_mock: bool = Field(
        description="True for generated demo data, never an official document."
    )
    created_at: datetime
    updated_at: datetime
    file_url: str = Field(description="API URL that opens the Markdown file inline.")
    download_url: str = Field(description="API URL that downloads the Markdown file.")


class DocumentSearchResult(DocumentRead):
    snippet: str | None = Field(
        default=None,
        description="Short excerpt around the first keyword match, if any.",
    )


class DocumentSearchResponse(BaseModel):
    items: list[DocumentSearchResult]
    total: int = Field(description="Total matching documents before pagination.")
    limit: int
    offset: int
