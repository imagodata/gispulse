"""
Centralized configuration for GISPulse.

All environment variables and hardcoded settings are defined here.
Adapters and core modules import from this file instead of accessing
environment variables directly.
"""

from __future__ import annotations

from typing import Dict, Optional
from pydantic import BaseSettings, Field


class StripeSettings(BaseSettings):
    """Stripe billing configuration."""
    api_key: Optional[str] = Field(None, env="GISPULSE_STRIPE_API_KEY")
    webhook_secret: Optional[str] = Field(None, env="GISPULSE_STRIPE_WEBHOOK_SECRET")
    
    # Price ID mappings
    price_pro_monthly: Optional[str] = Field(None, env="GISPULSE_STRIPE_PRICE_PRO_MONTHLY")
    price_pro_annual: Optional[str] = Field(None, env="GISPULSE_STRIPE_PRICE_PRO_ANNUAL")
    price_team_monthly: Optional[str] = Field(None, env="GISPULSE_STRIPE_PRICE_TEAM_MONTHLY")
    price_team_annual: Optional[str] = Field(None, env="GISPULSE_STRIPE_PRICE_TEAM_ANNUAL")
    
    # Maps (tier, interval) -> Stripe Price ID
    _PRICE_MAP: Dict[tuple[str, str], str] = {
        ("pro", "month"): price_pro_monthly,
        ("pro", "year"): price_pro_annual,
        ("team", "month"): price_team_monthly,
        ("team", "year"): price_team_annual,
    }

    def resolve_price_id(self, tier: str, interval: str) -> str:
        """Return the Stripe Price ID for a given tier and billing interval."""
        price_id = self._PRICE_MAP.get((tier.lower(), interval.lower()))
        if not price_id:
            raise ValueError(f"No Stripe Price ID configured for tier={tier}, interval={interval}")
        return price_id


class Settings(BaseSettings):
    """Global GISPulse settings."""
    stripe: StripeSettings = StripeSettings()

    class Config:
        env_nested_delimiter = "__"


# Singleton instance
settings = Settings()