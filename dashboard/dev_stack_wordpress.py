"""Backward-compatible imports — see dev_stack_app_urls.py."""

from __future__ import annotations

from dev_stack_app_urls import repair_wordpress_urls, stack_public_url as wordpress_public_url

__all__ = ["wordpress_public_url", "repair_wordpress_urls"]
