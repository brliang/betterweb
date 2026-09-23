"""The user's interests over the topic tree (PLAN.md §6.6).

An interest covers its topic and every descendant: a document tagged "Urban Planning" matches
an interest in its parent. Topics adjacent to an interest are its parent and its siblings (with
their descendants), unless an interest already covers them; they drive semantic exploration.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TopicTree:
    parents: Mapping[int, int | None]
    names: Mapping[int, str]
    children: Mapping[int, list[int]] = field(init=False)

    def __post_init__(self) -> None:
        children: defaultdict[int, list[int]] = defaultdict(list)
        for topic, parent in sorted(self.parents.items()):
            if parent is not None:
                children[parent].append(topic)
        object.__setattr__(self, "children", dict(children))

    def lineage(self, topic: int) -> list[int]:
        """The topic, then its parent, and so on up to its tier-1 root."""
        found = []
        current: int | None = topic
        while current is not None and current not in found:
            found.append(current)
            current = self.parents.get(current)
        return found

    def subtree(self, topic: int) -> list[int]:
        found = [topic]
        for descendant in found:
            found.extend(self.children.get(descendant, []))
        return found


@dataclass(frozen=True)
class Adjacency:
    shown: int
    """The adjacent topic named in the reason: the interest's parent or a sibling."""
    interest: int


class Interests:
    def __init__(self, tree: TopicTree, weights: Mapping[int, float]) -> None:
        self.tree = tree
        self.weights = {topic: weight for topic, weight in weights.items() if weight > 0}
        self._strongest = max(self.weights.values(), default=0.0)

    def covering(self, topic: int) -> int | None:
        """The nearest interest at or above the topic, if any."""
        return next((t for t in self.tree.lineage(topic) if t in self.weights), None)

    def weight(self, topic: int) -> float:
        covering = self.covering(topic)
        return 0.0 if covering is None else self.weights[covering]

    def overlap(self, tags: Iterable[tuple[int, float]]) -> float:
        """Tag-score-weighted share of the tags inside the interests, each counting its
        interest's weight relative to the strongest interest. 0 for untagged documents."""
        tags = list(tags)
        mass = sum(score for _, score in tags)
        if mass <= 0 or self._strongest <= 0:
            return 0.0
        inside = sum(score * self.weight(topic) for topic, score in tags)
        return inside / (mass * self._strongest)

    def adjacent(self) -> dict[int, Adjacency]:
        """Tag topic -> why it is adjacent, for every topic adjacent to an interest; the
        strongest interest wins when several are adjacent to one topic."""
        found: dict[int, Adjacency] = {}
        for interest in sorted(self.weights, key=lambda t: (-self.weights[t], t)):
            parent = self.tree.parents.get(interest)
            if parent is None:
                continue
            groups = [(parent, [parent])] + [
                (sibling, self.tree.subtree(sibling))
                for sibling in self.tree.children.get(parent, [])
                if sibling != interest
            ]
            for shown, topics in groups:
                for topic in topics:
                    if self.covering(topic) is None and topic not in found:
                        found[topic] = Adjacency(shown=shown, interest=interest)
        return found

    def covered(self) -> set[int]:
        """Every topic an interest covers."""
        return {topic for interest in self.weights for topic in self.tree.subtree(interest)}
