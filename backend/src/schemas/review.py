from pydantic import BaseModel,Field
class ReviewInput(BaseModel):
    note:str=Field(min_length=5,max_length=2000)
ReviewInput=ReviewInput
