
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Literal, Dict, Any

Lever = Literal["infrastructure","logistics","education","regulation","governance","trade","industry","finance","energy","security","automation"]
Direction = Literal["demand+","supply+","inflation+","inflation-","external+"]
Confidence = Literal["S","A","B","C","D"]

class Scale(BaseModel):
    value: Optional[float] = None
    unit: Optional[Literal["USD","LCU","%GDP","qty","unknown"]] = "unknown"

class Policy(BaseModel):
    title: str
    lever: List[Lever] = Field(default_factory=list)
    scale: Optional[Scale] = None
    direction: List[Direction] = Field(default_factory=list)
    lag_years: Optional[int] = None
    confidence: Confidence = "B"

class ExtractOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    horizon_years: int = 5
    policies: List[Policy]

JSON_SCHEMA = ExtractOutput.model_json_schema()
