from dragon_quant.scorers.drive import score as score_drive
from dragon_quant.scorers.anti_drop import score as score_anti_drop
from dragon_quant.scorers.leadership import score as score_leadership
from dragon_quant.scorers.absorption import score as score_absorption

SCORERS = {
    "drive":      (score_drive,      0.35),
    "anti_drop":  (score_anti_drop,  0.15),
    "leadership": (score_leadership, 0.25),
    "absorption": (score_absorption, 0.25),
}
