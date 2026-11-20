"""
Basic liveness: the person follows a random head-turn challenge, and the server checks that
the face in the camera frames moves the way a real head does. A flat photo or a screen held
up to the camera cannot: when it is tilted, the nose stays midway between the eyes.

This stops still images. It does not stop a replayed video of the right turns, a face-swap
tool, or software that feeds fake frames to the browser (see the README).
"""
import secrets
from dataclasses import dataclass

CENTER, LEFT, RIGHT = "center", "left", "right"


def new_challenge(turns, rng=None):
    """Look straight, then `turns` random turns with a look straight between them."""
    rng = rng or secrets.SystemRandom()
    steps = [CENTER]
    for i in range(turns):
        if i:
            steps.append(CENTER)
        steps.append(rng.choice((LEFT, RIGHT)))
    return steps


def turn(face):
    """
    How far the nose sits from the midpoint between the eyes, as a share of the eye distance.
    0 = facing the camera, positive = turned to the person's left, negative = to their right.
    Measured along the eye line, so a tilted head doesn't count as a turn.
    """
    ex, ey = face.left_eye[0] - face.right_eye[0], face.left_eye[1] - face.right_eye[1]
    span = ex * ex + ey * ey
    if span <= 0:
        return None
    nx, ny = face.nose[0] - face.right_eye[0], face.nose[1] - face.right_eye[1]
    return (nx * ex + ny * ey) / span - 0.5


@dataclass(frozen=True)
class Verdict:
    passed: bool
    picks: tuple = ()  # for each step, the index of the frame that best shows it


def judge(steps, turns_by_step, turn_min, center_max):
    """turns_by_step[i] holds turn() for every usable frame captured during steps[i]."""
    picks = []
    for step, turns in zip(steps, turns_by_step):
        if not turns:
            return Verdict(False)
        if step == CENTER:
            best = min(range(len(turns)), key=lambda i: abs(turns[i]))
            passed = abs(turns[best]) <= center_max
        elif step == LEFT:
            best = max(range(len(turns)), key=lambda i: turns[i])
            passed = turns[best] >= turn_min
        else:
            best = min(range(len(turns)), key=lambda i: turns[i])
            passed = turns[best] <= -turn_min
        if not passed:
            return Verdict(False)
        picks.append(best)
    return Verdict(len(picks) == len(steps), tuple(picks))
