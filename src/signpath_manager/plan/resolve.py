"""Resolution of principal references (emails, group names, CI user names) to SignPath IDs."""

from __future__ import annotations

from dataclasses import dataclass

from signpath_manager.api.models import is_inactive
from signpath_manager.config.desired import Principal
from signpath_manager.live.state import LiveCIUser, LiveState

PENDING_PREFIX = "new:ci:"


def pending_ci_id(name: str) -> str:
    """Placeholder ID of a CI user that will be created during apply."""
    return f"{PENDING_PREFIX}{name}"


def is_pending(id_: str) -> bool:
    return id_.startswith(PENDING_PREFIX)


@dataclass
class Resolution:
    id: str | None
    error: str | None = None
    warning: str | None = None


class Resolver:
    def __init__(self, live: LiveState) -> None:
        self.live = live
        self._users_by_email: dict[str, list[str]] = {}
        for user in live.interactive_users.values():
            for email in user.emails:
                ids = self._users_by_email.setdefault(email.casefold(), [])
                if user.user_id not in ids:
                    ids.append(user.user_id)
        self._groups_by_name: dict[str, list[str]] = {}
        for g in live.group_list:
            self._groups_by_name.setdefault(g.name.casefold(), []).append(g.id)
        self._labels: dict[str, str] = {}
        for p in live.interactive_user_list:
            detail = live.interactive_users.get(p.id)
            self._labels[p.id] = (detail.emails[0] if detail and detail.emails else None) or p.name
        for g in live.group_list:
            self._labels[g.id] = f"group:{g.name}"
        for c in live.ci_user_list:
            self._labels[c.id] = f"ci:{c.name}"
        self.pending: dict[str, str] = {}  # casefolded CI user name -> pending id

    # -------------------------------------------------------------- CI users

    def match_ci_user(self, name: str) -> tuple[LiveCIUser | None, str | None]:
        """Find the live CI user for a declared name: the active one, else a single inactive one."""
        candidates = self.live.ci_users_named(name)
        active = [u for u in candidates if u.active]
        if active:
            return active[0], None
        if len(candidates) == 1:
            return candidates[0], None
        if len(candidates) > 1:
            return None, (
                f"several inactive CI users are named '{name}'; rename or remove the extra ones in SignPath"
            )
        return None, None

    def plan_ci_creation(self, name: str) -> str:
        pid = pending_ci_id(name)
        self.pending[name.casefold()] = pid
        self._labels[pid] = f"ci:{name} (new)"
        return pid

    # -------------------------------------------------------------- principals

    def resolve(self, principal: Principal) -> Resolution:
        if principal.kind == "user":
            ids = self._users_by_email.get(principal.value.casefold(), [])
            if not ids:
                return Resolution(None, f"no interactive user with email '{principal.value}' in SignPath")
            if len(ids) > 1:
                return Resolution(None, f"email '{principal.value}' matches several interactive users")
            user = self.live.interactive_users[ids[0]]
            warning = (
                f"interactive user '{principal.value}' is inactive" if is_inactive(user.status) else None
            )
            return Resolution(ids[0], warning=warning)
        if principal.kind == "group":
            ids = self._groups_by_name.get(principal.value.casefold(), [])
            if not ids:
                return Resolution(None, f"no user group named '{principal.value}' in SignPath")
            if len(ids) > 1:
                return Resolution(None, f"several user groups are named '{principal.value}'")
            group = self.live.groups.get(ids[0])
            warning = (
                f"user group '{principal.value}' is inactive"
                if group is not None and is_inactive(group.status)
                else None
            )
            return Resolution(ids[0], warning=warning)
        pending = self.pending.get(principal.value.casefold())
        if pending is not None:
            return Resolution(pending)
        ci_user, error = self.match_ci_user(principal.value)
        if error:
            return Resolution(None, error)
        if ci_user is None:
            return Resolution(None, f"no CI user named '{principal.value}' in SignPath")
        warning = f"CI user '{principal.value}' is inactive" if not ci_user.active else None
        return Resolution(ci_user.id, warning=warning)

    def label(self, id_: str) -> str:
        return self._labels.get(id_, id_)

    def labels(self, ids: set[str] | list[str]) -> list[str]:
        return sorted(self.label(i) for i in ids)
