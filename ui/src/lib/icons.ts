// Phosphor icons (regular weight), bundled at build time. Only names listed here ship.

import house from "@phosphor-icons/core/assets/regular/house.svg?raw";
import scroll from "@phosphor-icons/core/assets/regular/scroll.svg?raw";
import clockCounterClockwise from "@phosphor-icons/core/assets/regular/clock-counter-clockwise.svg?raw";
import users from "@phosphor-icons/core/assets/regular/users.svg?raw";
import chartBar from "@phosphor-icons/core/assets/regular/chart-bar.svg?raw";
import shieldCheck from "@phosphor-icons/core/assets/regular/shield-check.svg?raw";
import gearSix from "@phosphor-icons/core/assets/regular/gear-six.svg?raw";
import cpu from "@phosphor-icons/core/assets/regular/cpu.svg?raw";
import coins from "@phosphor-icons/core/assets/regular/coins.svg?raw";
import broadcast from "@phosphor-icons/core/assets/regular/broadcast.svg?raw";
import plus from "@phosphor-icons/core/assets/regular/plus.svg?raw";
import x from "@phosphor-icons/core/assets/regular/x.svg?raw";
import trash from "@phosphor-icons/core/assets/regular/trash.svg?raw";
import magnifyingGlass from "@phosphor-icons/core/assets/regular/magnifying-glass.svg?raw";
import check from "@phosphor-icons/core/assets/regular/check.svg?raw";
import warning from "@phosphor-icons/core/assets/regular/warning.svg?raw";
import warningCircle from "@phosphor-icons/core/assets/regular/warning-circle.svg?raw";
import arrowCounterClockwise from "@phosphor-icons/core/assets/regular/arrow-counter-clockwise.svg?raw";
import arrowClockwise from "@phosphor-icons/core/assets/regular/arrow-clockwise.svg?raw";
import twitchLogo from "@phosphor-icons/core/assets/regular/twitch-logo.svg?raw";
import key from "@phosphor-icons/core/assets/regular/key.svg?raw";
import eye from "@phosphor-icons/core/assets/regular/eye.svg?raw";
import eyeSlash from "@phosphor-icons/core/assets/regular/eye-slash.svg?raw";
import copy from "@phosphor-icons/core/assets/regular/copy.svg?raw";
import arrowSquareOut from "@phosphor-icons/core/assets/regular/arrow-square-out.svg?raw";
import pause from "@phosphor-icons/core/assets/regular/pause.svg?raw";
import play from "@phosphor-icons/core/assets/regular/play.svg?raw";
import userPlus from "@phosphor-icons/core/assets/regular/user-plus.svg?raw";
import prohibit from "@phosphor-icons/core/assets/regular/prohibit.svg?raw";
import info from "@phosphor-icons/core/assets/regular/info.svg?raw";
import caretDown from "@phosphor-icons/core/assets/regular/caret-down.svg?raw";
import caretRight from "@phosphor-icons/core/assets/regular/caret-right.svg?raw";
import lightning from "@phosphor-icons/core/assets/regular/lightning.svg?raw";
import pencilSimple from "@phosphor-icons/core/assets/regular/pencil-simple.svg?raw";
import table from "@phosphor-icons/core/assets/regular/table.svg?raw";
import circleNotch from "@phosphor-icons/core/assets/regular/circle-notch.svg?raw";
import checkCircle from "@phosphor-icons/core/assets/regular/check-circle.svg?raw";
import robot from "@phosphor-icons/core/assets/regular/robot.svg?raw";
import gavel from "@phosphor-icons/core/assets/regular/gavel.svg?raw";
import hourglass from "@phosphor-icons/core/assets/regular/hourglass.svg?raw";
import signOut from "@phosphor-icons/core/assets/regular/sign-out.svg?raw";
import puzzlePiece from "@phosphor-icons/core/assets/regular/puzzle-piece.svg?raw";
import star from "@phosphor-icons/core/assets/regular/star.svg?raw";
import userCheck from "@phosphor-icons/core/assets/regular/user-check.svg?raw";
import listBullets from "@phosphor-icons/core/assets/regular/list-bullets.svg?raw";
import funnel from "@phosphor-icons/core/assets/regular/funnel.svg?raw";
import arrowRight from "@phosphor-icons/core/assets/regular/arrow-right.svg?raw";
import chatText from "@phosphor-icons/core/assets/regular/chat-text.svg?raw";
import sealCheck from "@phosphor-icons/core/assets/regular/seal-check.svg?raw";

export const ICONS = {
  "house": house,
  "scroll": scroll,
  "clock-counter-clockwise": clockCounterClockwise,
  "users": users,
  "chart-bar": chartBar,
  "shield-check": shieldCheck,
  "gear-six": gearSix,
  "cpu": cpu,
  "coins": coins,
  "broadcast": broadcast,
  "plus": plus,
  "x": x,
  "trash": trash,
  "magnifying-glass": magnifyingGlass,
  "check": check,
  "warning": warning,
  "warning-circle": warningCircle,
  "arrow-counter-clockwise": arrowCounterClockwise,
  "arrow-clockwise": arrowClockwise,
  "twitch-logo": twitchLogo,
  "key": key,
  "eye": eye,
  "eye-slash": eyeSlash,
  "copy": copy,
  "arrow-square-out": arrowSquareOut,
  "pause": pause,
  "play": play,
  "user-plus": userPlus,
  "prohibit": prohibit,
  "info": info,
  "caret-down": caretDown,
  "caret-right": caretRight,
  "lightning": lightning,
  "pencil-simple": pencilSimple,
  "table": table,
  "circle-notch": circleNotch,
  "check-circle": checkCircle,
  "robot": robot,
  "gavel": gavel,
  "hourglass": hourglass,
  "sign-out": signOut,
  "puzzle-piece": puzzlePiece,
  "star": star,
  "user-check": userCheck,
  "list-bullets": listBullets,
  "funnel": funnel,
  "arrow-right": arrowRight,
  "chat-text": chatText,
  "seal-check": sealCheck,
} as const;

export type IconName = keyof typeof ICONS;
