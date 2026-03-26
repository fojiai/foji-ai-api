from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AIModel(Base):
    __tablename__ = "AIModels"

    id: Mapped[int] = mapped_column("Id", Integer, primary_key=True)
    name: Mapped[str] = mapped_column("Name", String(100))
    display_name: Mapped[str] = mapped_column("DisplayName", String(150))
    provider: Mapped[str] = mapped_column("Provider", String(20))
    model_id: Mapped[str] = mapped_column("ModelId", String(100))
    input_cost_per_1m: Mapped[float] = mapped_column("InputCostPer1M", Numeric(10, 4))
    output_cost_per_1m: Mapped[float] = mapped_column("OutputCostPer1M", Numeric(10, 4))
    is_active: Mapped[bool] = mapped_column("IsActive", Boolean)
    is_default: Mapped[bool] = mapped_column("IsDefault", Boolean)
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime)
    updated_at: Mapped[datetime] = mapped_column("UpdatedAt", DateTime)
