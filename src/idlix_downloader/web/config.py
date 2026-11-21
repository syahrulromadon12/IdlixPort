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
        "g6.akademivo.website",
        "g6.aspireheightsacademy.digital",
        "g7.aspireheightsacademy.digital",
        "g7.akademivo.website",
        "g6.edubambu.store",
        "g7.edubambu.store",
        "g6.horizonacademy.site",
        "g7.horizonacademy.site",
        "g6.edusparkonline.quest",
        "g7.edusparkonline.quest",
        "g6.eldrinlorekeeper.store",
        "g7.eldrinlorekeeper.store",
        "g6.knowledgequest.store",
        "g7.knowledgequest.store",
        "g6.learnleapinstitute.site",
        "g7.learnleapinstitute.site",
        "g6.mindbloomcourses.website",
        "g7.mindbloomcourses.website",
        "g6.skillsprouthub.space",
        "g7.skillsprouthub.space",
        "g6.onlinestudy.online",
        "g7.onlinestudy.online",
        "g6.academiclearning.site",
        "g7.academiclearning.site",
        "g6.classroomlearning.space",
        "g7.classroomlearning.space",
        "g6.careerskills.site",
        "g7.careerskills.site",
        "g6.distancelearning.space",
        "g7.distancelearning.space",
        "g6.educationalskills.space",
        "g7.educationalskills.space",
        "g6.educationlearning.store",
        "g7.educationlearning.store",
        "g6.educationonlinelearning.xyz",
        "g7.educationonlinelearning.xyz",
        "g6.e-learningtips.site",
        "g7.e-learningtips.site",
        "g6.exampreparation.space",
        "g7.exampreparation.space",
        "g6.highereducation.online",
        "g7.highereducation.online",
        "g6.homeworkhelp.site",
        "g7.homeworkhelp.site",
        "g6.languagelearning.space",
        "g7.languagelearning.space",
        "g6.onlineeducation.store",
        "g7.onlineeducation.store",
        "g6.scholarshipeducation.online",
        "g7.scholarshipeducation.online",
        "g6.skilldevelopment.space",
        "g7.skilldevelopment.space",
        "g6.stemeducation.site",
        "g7.stemeducation.site",
        "g6.studentsuccess.space",
        "g7.studentsuccess.space",
        "g6.studycourses.site",
        "g7.studycourses.site",
        "g6.tutoringskills.site",
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
