from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class ReelBatch:
    id: int
    item_number: int
    order_number: str
    edited: bool
    state: str
    time_arrived: str
    video_out_dir: str
    add_music: bool
    splits: int
    concat: bool
    film_type: str
    version: int
    video_name: str
    file_type: str
    video_dir: str
    title: str
    subtitle: str
    qc_data: List[Dict[str, Any]] = field(default_factory=list)
    preview_loaded: bool = False
    increase_fps: bool = False
    single_dvd: bool = False
    multi_dvd: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
