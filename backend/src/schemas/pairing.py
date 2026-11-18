from pydantic import BaseModel,Field


class ReadingInput(BaseModel):
    process:str=Field(min_length=1,max_length=10)
    label:str=Field(min_length=1,max_length=100)
    value:float
    measured_at:str=Field(min_length=1,max_length=40)


class PairingPreviewInput(BaseModel):
    max_gap_seconds:float


class ReadingRow(BaseModel):
    label:str=Field(min_length=1,max_length=100)
    value:float
    measured_at:str=Field(min_length=1,max_length=40)


class ReadingBatchInput(BaseModel):
    process:str=Field(min_length=1,max_length=10)
    rows:list[ReadingRow]=Field(min_length=1,max_length=2000)
