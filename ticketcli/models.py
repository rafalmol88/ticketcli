from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, List


@dataclass
class Attachment:
    id: str
    name: str
    download_url: Optional[str] = None
    size: Optional[int] = None

    @classmethod
    def from_dict(cls, data: dict) -> "Attachment":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            download_url=data.get("download_url"),
            size=data.get("size"),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "download_url": self.download_url,
            "size": self.size,
        }


@dataclass
class Comment:
    id: str
    author: str
    body: str
    created_at: Optional[str] = None
    pinned: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "Comment":
        return cls(
            id=data.get("id", ""),
            author=data.get("author", ""),
            body=data.get("body", ""),
            created_at=data.get("created_at"),
            pinned=bool(data.get("pinned", False)),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "author": self.author,
            "body": self.body,
            "created_at": self.created_at,
            "pinned": self.pinned,
        }


@dataclass
class Worklog:
    id: str
    author: str
    body: str
    time_spent: Optional[str] = None
    created_at: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "Worklog":
        return cls(
            id=data.get("id", ""),
            author=data.get("author", ""),
            body=data.get("body", ""),
            time_spent=data.get("time_spent"),
            created_at=data.get("created_at"),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "author": self.author,
            "body": self.body,
            "time_spent": self.time_spent,
            "created_at": self.created_at,
        }


@dataclass
class IssueLink:
    """A directed link between two issues."""
    link_type: str
    outward_key: str
    outward_summary: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "IssueLink":
        return cls(
            link_type=data.get("link_type", ""),
            outward_key=data.get("outward_key", ""),
            outward_summary=data.get("outward_summary"),
        )

    def to_dict(self) -> dict:
        return {
            "link_type": self.link_type,
            "outward_key": self.outward_key,
            "outward_summary": self.outward_summary,
        }


@dataclass
class ChangelogEntry:
    """One field-level change from an issue's history."""
    field: str
    from_value: Optional[str] = None
    to_value: Optional[str] = None
    author: Optional[str] = None
    created_at: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "ChangelogEntry":
        return cls(
            field=data.get("field", ""),
            from_value=data.get("from_value"),
            to_value=data.get("to_value"),
            author=data.get("author"),
            created_at=data.get("created_at"),
        )

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "from_value": self.from_value,
            "to_value": self.to_value,
            "author": self.author,
            "created_at": self.created_at,
        }


@dataclass
class Issue:
    key: str
    summary: str = ""
    description: str = ""
    status: str = ""
    assignee: str = ""
    creator: str = ""
    id: str = ""
    labels: List[str] = field(default_factory=list)
    components: List[str] = field(default_factory=list)
    attachments: List[Attachment] = field(default_factory=list)
    comments: List[Comment] = field(default_factory=list)
    worklogs: List[Worklog] = field(default_factory=list)
    worklogs_available: bool = True
    links: List[IssueLink] = field(default_factory=list)
    changelog: List[ChangelogEntry] = field(default_factory=list)
    raw: Any = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, data: dict) -> "Issue":
        return cls(
            key=data.get("key") or data.get("issue_key", ""),
            summary=data.get("summary", ""),
            description=data.get("description", ""),
            status=data.get("status", ""),
            assignee=data.get("assignee", ""),
            creator=data.get("creator", ""),
            id=data.get("id", ""),
            labels=list(data.get("labels") or []),
            components=list(data.get("components") or []),
            attachments=[Attachment.from_dict(a) for a in data.get("attachments", [])],
            comments=[Comment.from_dict(c) for c in data.get("comments", [])],
            worklogs=[Worklog.from_dict(w) for w in data.get("worklogs", [])],
            worklogs_available=bool(data.get("worklogs_available", True)),
            links=[IssueLink.from_dict(l) for l in data.get("links", [])],
            changelog=[ChangelogEntry.from_dict(e) for e in data.get("changelog", [])],
        )

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "summary": self.summary,
            "description": self.description,
            "status": self.status,
            "assignee": self.assignee,
            "creator": self.creator,
            "id": self.id,
            "labels": list(self.labels),
            "components": list(self.components),
            "attachments": [a.to_dict() for a in self.attachments],
            "comments": [c.to_dict() for c in self.comments],
            "worklogs": [w.to_dict() for w in self.worklogs],
            "worklogs_available": self.worklogs_available,
            "links": [l.to_dict() for l in self.links],
            "changelog": [e.to_dict() for e in self.changelog],
        }
        
@dataclass
class IssueListItem:
    key: str
    summary: str
    assignee: Optional[str] = None
    status: Optional[str] = None
    labels: List[str] = field(default_factory=list)
    components: List[str] = field(default_factory=list)
