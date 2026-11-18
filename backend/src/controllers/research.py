from src.services import studies,runs,pairing
# Controllers keep endpoint contracts separate from persistence and calculation.
def overview(db): return {'studies':studies.list_studies(db),'runs':runs.list_runs(db)}
def create_study(db,data,user): return studies.create(db,data,user)
def list_rows(db,id): return studies.observations(db,id)
def save_row(db,id,data,user,row_id=None): return studies.save_observation(db,id,data,user,row_id)
def create_run(db,data,user): return runs.create(db,data,user)
def review_run(db,id,data,user): return runs.approve(db,id,data,user)
def list_readings(db,id): return pairing.list_readings(db,id)
def save_reading(db,id,data,user,row_id=None): return pairing.save_reading(db,id,data,user,row_id)
def save_reading_batch(db,id,data,user): return pairing.save_reading_batch(db,id,data,user)
def delete_reading(db,id,row_id,user): return pairing.delete_reading(db,id,row_id,user)
def create_preview(db,id,data,user): return pairing.create_preview(db,id,data,user)
def list_previews(db,id): return pairing.list_previews(db,id)
def get_preview(db,id): return pairing.get_preview(db,id)
def confirm_preview(db,id,user): return pairing.confirm_preview(db,id,user)
def active_pairing(db,id): return pairing.active_pairing(db,id)
