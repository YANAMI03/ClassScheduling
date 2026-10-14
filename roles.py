"""Single source of truth for user roles.

The role that used to be called "Scheduler" is now "Chair" (a PURE RENAME: same
permissions, same program scoping).  Only three roles exist in this system:

    ROLE_ADMIN  = 'admin'
    ROLE_CHAIR  = 'chair'   (formerly 'scheduler')
    ROLE_VIEWER = 'viewer'

Every layer -- Flask decorators, the central permission map, the sidebar,
templates, the Users tab whitelist/dropdown, sign-up, the activity log, backup
sanitisation and the automated access audit -- must import the constants and
helpers below instead of hard-coding role strings.

This module is deliberately free of Flask / Supabase imports so it can be used
from anywhere (including plain unit tests).
"""

from __future__ import annotations

# ── Canonical stored values ────────────────────────────────────────────────────
ROLE_ADMIN = 'admin'
ROLE_CHAIR = 'chair'
ROLE_VIEWER = 'viewer'

VALID_ROLES = frozenset({ROLE_ADMIN, ROLE_CHAIR, ROLE_VIEWER})

# Human-readable display labels.  The stored value stays 'chair'; only the label
# maps to "Chair".
ROLE_LABELS = {
    ROLE_ADMIN: 'Admin',
    ROLE_CHAIR: 'Chair',
    ROLE_VIEWER: 'Viewer',
}

# ── Legacy / alias normalisation ───────────────────────────────────────────────
# Keys are already lower-cased and have spaces / dashes collapsed to underscores
# (see ``slugify``).  Every value is a canonical role.
ROLE_ALIASES = {
    # Admin family
    'admin': ROLE_ADMIN,
    'super_admin': ROLE_ADMIN,
    'superadmin': ROLE_ADMIN,
    'super_admin_role': ROLE_ADMIN,
    'administrator': ROLE_ADMIN,
    'academic_admin': ROLE_ADMIN,
    # Chair family (the former "Scheduler", the old "Dean" and old "Chair")
    'chair': ROLE_CHAIR,
    'scheduler': ROLE_CHAIR,
    'program_scheduler': ROLE_CHAIR,
    'program_chair': ROLE_CHAIR,
    'department_head': ROLE_CHAIR,
    'dean': ROLE_CHAIR,
    'dean_chair': ROLE_CHAIR,
    # Viewer family
    'viewer': ROLE_VIEWER,
    'instructor': ROLE_VIEWER,
    'guest': ROLE_VIEWER,
    'faculty': ROLE_VIEWER,
    'teacher': ROLE_VIEWER,
}

# Temporary transition map: legacy stored values -> new stored values.
# Only the pure rename is here.  Used by the login/session normaliser (marked for
# removal once every account and every policy/function has been migrated to
# 'chair') and by the Backup & Restore sanitiser so old backup files restore
# cleanly.
LEGACY_STORED_ROLES = {
    'scheduler': ROLE_CHAIR,
    'dean': ROLE_CHAIR,
}


def slugify(value) -> str:
    """Lower-case, trim and collapse spaces/dashes to underscores.

    ``'Dean/Chair'`` -> ``'dean/chair'`` (the slash is preserved because legacy
    accounts used it), ``'Super Admin'`` -> ``'super_admin'``.
    """
    if value is None:
        return ''
    text = str(value).strip().lower().replace('-', '_').replace(' ', '_')
    # Collapse an internal slash into an underscore alias handled below.
    return text


def normalize_role(value) -> str:
    """Return the canonical role for ``value``.

    Known aliases (including the legacy ``scheduler``, ``dean``, ``super_admin``,
    ``instructor`` ...) are mapped to ``admin`` / ``chair`` / ``viewer``.  An
    unknown value is returned as a normalised slug so callers can detect and
    deny it (never silently promoted).
    """
    slug = slugify(value)
    if not slug:
        return ''
    if slug in ROLE_ALIASES:
        return ROLE_ALIASES[slug]
    # 'dean/chair' slugifies to 'dean/chair'; check the slash form too.
    slash = slug.replace('/', '_')
    if slash in ROLE_ALIASES:
        return ROLE_ALIASES[slash]
    return slug


# Common misspelling kept for backwards compatibility with older callers.
normalize = normalize_role


def is_known_role(value) -> bool:
    """True when ``value`` maps to one of the three canonical roles."""
    return normalize_role(value) in VALID_ROLES


def role_label(value) -> str:
    """Display label for a role value ('chair' -> 'Chair')."""
    canonical = normalize_role(value)
    if canonical in ROLE_LABELS:
        return ROLE_LABELS[canonical]
    if not canonical:
        return 'Viewer'
    return canonical.replace('_', ' ').replace('/', ' ').title()


def sanitize_stored_role(value, default: str = ROLE_VIEWER) -> str:
    """Coerce a role destined for the ``users`` table.

    Legacy values (``scheduler`` / ``dean``) become ``chair``; anything that is
    not admin / chair / viewer falls back to ``default``.  Used by Backup &
    Restore so a restored file can never introduce a role outside the whitelist.
    """
    canonical = normalize_role(value)
    if canonical in VALID_ROLES:
        return canonical
    return default


def role_for_restore(value) -> str:
    """Map an old backup's stored role value to the current stored value.

    ``'scheduler'`` -> ``'chair'`` (pure rename).  Unknown roles are coerced to
    ``viewer`` so a restore can never create a privilege outside the whitelist.
    """
    return sanitize_stored_role(value, default=ROLE_VIEWER)
