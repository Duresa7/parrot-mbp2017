"""Small, stream-injectable terminal interface."""

import sys
from typing import Iterable, TextIO


class UI:
    def __init__(self, *, color: bool, assume_yes: bool, interactive: bool,
                 out: TextIO | None = None, inp: TextIO | None = None):
        self.out = sys.stdout if out is None else out
        self.inp = sys.stdin if inp is None else inp
        self.color = color
        self.assume_yes = assume_yes
        self.interactive = interactive
        encoding = (getattr(self.out, "encoding", None) or "utf-8").lower().replace("-", "")
        self.unicode = encoding in ("utf8", "utf_8")

    def _print(self, text: str, code: str = "", symbol: str = "", fallback: str = "") -> None:
        prefix = symbol if self.unicode else fallback
        line = f"{prefix} {text}" if prefix else text
        if not self.unicode:
            line = line.replace("—", "-").replace("–", "-")
            encoding = getattr(self.out, "encoding", None) or "ascii"
            line = line.encode(encoding, errors="replace").decode(encoding)
        if self.color and code:
            line = f"\033[{code}m{line}\033[0m"
        print(line, file=self.out)

    def heading(self, text: str) -> None:
        self._print(text, "1")

    def info(self, text: str) -> None:
        self._print(text)

    def ok(self, text: str) -> None:
        self._print(text, "32", "✓", "OK:")

    def warn(self, text: str) -> None:
        self._print(text, "33", "⚠", "Warning:")

    def error(self, text: str) -> None:
        self._print(text, "31", "✗", "Error:")

    def step(self, text: str) -> None:
        self._print(text, "36", "→", "->")

    def detail(self, text: str) -> None:
        self._print("  " + text)

    def table(self, headers: Iterable[str], rows: Iterable[Iterable[object]]) -> None:
        values = [[str(value) for value in headers]] + [[str(value) for value in row] for row in rows]
        widths = [max(len(row[i]) for row in values) for i in range(len(values[0]))]
        for row in values:
            self._print("  ".join(value.ljust(width) for value, width in zip(row, widths)).rstrip())

    def confirm(self, question: str, default: bool = True) -> bool:
        if self.assume_yes:
            return True
        if not self.interactive:
            return default
        while True:
            self._print(question + (" [Y/n]" if default else " [y/N]"))
            answer = self.inp.readline().strip().lower()
            if not answer:
                return default
            if answer in ("y", "yes", "n", "no"):
                return answer in ("y", "yes")
            self.warn("Please type yes or no.")

    def toggle(self, items: Iterable, selected: set) -> set:
        """Items may be fix objects, (id, label) pairs, or plain IDs."""
        pairs = []
        for item in items:
            if hasattr(item, "id"):
                pairs.append((item.id, item.title))
            elif isinstance(item, tuple):
                pairs.append(item)
            else:
                pairs.append((item, str(item)))
        selected = set(selected)
        while True:
            for index, (key, label) in enumerate(pairs, 1):
                self.info(f"{index}. [{'x' if key in selected else ' '}] {label}")
            if self.assume_yes or not self.interactive:
                return selected
            self.info("Type numbers to toggle (separated by spaces), or press Enter to accept:")
            answer = self.inp.readline().strip()
            if not answer:
                return selected
            try:
                numbers = [int(value) for value in answer.replace(",", " ").split()]
                if any(number < 1 or number > len(pairs) for number in numbers):
                    raise ValueError
            except ValueError:
                self.warn("Please use the numbers in the list.")
                continue
            for number in numbers:
                key = pairs[number - 1][0]
                selected.symmetric_difference_update({key})
