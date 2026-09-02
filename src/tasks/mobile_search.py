from src.tasks.base_task import BaseSearchTask


class MobileSearchTask(BaseSearchTask):
    device_name = "mobile"
    count_key = "mobile_count"
