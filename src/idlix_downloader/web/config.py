"""Operator settings; the browser cannot choose filesystem or transport policy."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def default_data_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/IdlixPort"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "IdlixPort"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "idlixport"


@dataclass(frozen=True)
class WebConfig:
    data_dir: Path
    port: int = 8765
    allowed_hosts: tuple[str, ...] = (
        "z3.idlixku.com",
        "majorplay.net",
        ".majorplay.net",
        "g2.akademivo.website",
        "g7.aspireheightsacademy.digital",
        "g7.akademivo.website",
        "g7.edubambu.store",
        "g7.horizonacademy.site",
        "g7.edusparkonline.quest",
        "g7.eldrinlorekeeper.store",
        "g7.knowledgequest.store",
        "g7.learnleapinstitute.site",
        "g7.mindbloomcourses.website",
        "g7.skillsprouthub.space",
        "g7.onlinestudy.online",
        "g7.academiclearning.site",
        "g7.classroomlearning.space",
        "g7.careerskills.site",
        "g7.distancelearning.space",
        "g7.educationalskills.space",
        "g7.educationlearning.store",
        "g7.educationonlinelearning.xyz",
        "g7.e-learningtips.site",
        "g7.exampreparation.space",
        "g7.highereducation.online",
        "g7.homeworkhelp.site",
        "g7.languagelearning.space",
        "g7.onlineeducation.store",
        "g7.scholarshipeducation.online",
        "g7.skilldevelopment.space",
        "g7.stemeducation.site",
        "g7.studentsuccess.space",
        "g7.studycourses.site",
        "g7.tutoringskills.site",
    )
    queue_limit: int = 20
    network_slots: int = 4
    download_dirs: tuple[Path, ...] = ()

    def __post_init__(self):
        if not 1 <= self.port <= 65535 or not 1 <= self.queue_limit <= 20:
            raise ValueError("Invalid web capacity or port")
        if not 1 <= self.network_slots <= 8:
            raise ValueError("Invalid network capacity")

    @property
    def origins(self):
        return {f"http://{host}:{self.port}" for host in ("127.0.0.1", "localhost")}
