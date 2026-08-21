from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
import re
import statistics
from typing import Any, Iterable

WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z'’-]*|\d+(?:\.\d+)?")
SENTENCE_RE = re.compile(r"[.!?]+")

EMOTIONAL_WORDS = {
    "love", "joy", "delight", "happy", "proud", "relief", "confidence", "confident",
    "fear", "safe", "secure", "excited", "inspired", "belong", "hope", "comfort",
    "stress", "calm", "peace", "energized", "bold", "freedom", "win", "worry",
    "overwhelmed", "overloaded", "celebrate", "care", "trusted", "trust", "dream",
}

CTA_WORDS = {
    "buy", "try", "start", "join", "sign", "subscribe", "download", "book", "shop",
    "learn", "discover", "get", "claim", "reserve", "order", "visit", "call", "save",
    "register", "apply", "schedule", "unlock", "choose", "compare",
}

CLICHES = {
    "game changer", "next level", "best in class", "revolutionary", "innovative solution",
    "cutting edge", "world class", "unleash", "unlock your potential", "like never before",
    "seamless experience", "transform your", "future of", "one stop shop", "supercharge",
    "elevate your", "disrupt", "ultimate", "premium quality", "at your fingertips",
}

DIFFERENTIATORS = {
    "only", "first", "exclusive", "patented", "proprietary", "unique", "unlike", "instead",
    "without", "because", "proof", "certified", "guaranteed", "personalized", "custom",
    "15-minute", "15", "faster", "less", "more", "specific", "local", "independent",
}

RISK_TERMS = {
    "guaranteed": "Absolute guarantee may need substantiation.",
    "cure": "Medical or performance cure claim may be regulated.",
    "miracle": "Miracle-style claims can be misleading.",
    "free": "Free claims may require clear conditions.",
    "best": "Superlative claim may require evidence.",
    "#1": "Ranking claim may require current proof.",
    "number one": "Ranking claim may require current proof.",
    "risk-free": "Risk-free claim may require clear terms.",
    "never": "Absolute claim can create substantiation risk.",
    "always": "Absolute claim can create substantiation risk.",
    "everyone": "Overbroad audience claim can be risky.",
    "no strings": "Offer condition claim may need disclosure.",
}

JARGON = {
    "synergy", "leverage", "ecosystem", "omnichannel", "paradigm", "stakeholder",
    "frictionless", "enablement", "optimization", "holistic", "verticalized", "scalable",
    "personalization", "integrated", "turnkey", "solutioning",
}

STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "for", "to", "of", "in", "on",
    "with", "by", "from", "at", "as", "is", "are", "was", "were", "be", "been", "it",
    "this", "that", "these", "those", "your", "our", "you", "we", "they", "them", "their",
    "about", "into", "over", "under", "up", "down", "out", "today", "now", "new",
}

WEIGHTS = {
    "clarity": 0.15,
    "originality": 0.15,
    "audience_fit": 0.15,
    "emotional_impact": 0.15,
    "differentiation": 0.15,
    "cta_strength": 0.10,
    "risk_safety": 0.15,
}


@dataclass(frozen=True)
class CriterionComparison:
    criterion: str
    label: str
    score_a: float
    score_b: float
    winner: str
    rationale_a: str
    rationale_b: str


@dataclass(frozen=True)
class CreativeScorecard:
    name_a: str
    name_b: str
    audience: str
    objective: str
    generated_at: str
    criteria: list[CriterionComparison]
    weighted_total_a: float
    weighted_total_b: float
    recommendation: str
    summary: str
    suggested_next_steps: list[str]
    notes: list[str]


class CreativeCompareAgent:
    """Deterministic local agent for comparing two creative concepts."""

    def compare(
        self,
        creative_a: str,
        creative_b: str,
        *,
        audience: str = "",
        objective: str = "",
        name_a: str = "Creative A",
        name_b: str = "Creative B",
    ) -> CreativeScorecard:
        """Compare two pieces of creative content and return a structured scorecard."""
        if not creative_a or not creative_a.strip():
            raise ValueError("creative_a must not be empty")
        if not creative_b or not creative_b.strip():
            raise ValueError("creative_b must not be empty")

        metrics_a = self._analyze(creative_a, audience)
        metrics_b = self._analyze(creative_b, audience)

        comparisons = [
            self._criterion("clarity", "Clarity", metrics_a, metrics_b),
            self._criterion("originality", "Originality", metrics_a, metrics_b),
            self._criterion("audience_fit", "Audience fit", metrics_a, metrics_b),
            self._criterion("emotional_impact", "Emotional impact", metrics_a, metrics_b),
            self._criterion("differentiation", "Differentiation", metrics_a, metrics_b),
            self._criterion("cta_strength", "CTA strength", metrics_a, metrics_b),
            self._criterion("risk_safety", "Risk / safety", metrics_a, metrics_b),
        ]

        total_a = round(sum(c.score_a * WEIGHTS[c.criterion] for c in comparisons), 2)
        total_b = round(sum(c.score_b * WEIGHTS[c.criterion] for c in comparisons), 2)
        delta = round(abs(total_a - total_b), 2)

        if delta < 0.35:
            recommendation = "Tie / needs human judgment"
            summary = (
                f"{name_a} and {name_b} are closely matched ({total_a} vs {total_b}). "
                "Use the criterion-level notes to combine the strongest elements."
            )
        elif total_a > total_b:
            recommendation = name_a
            summary = (
                f"Recommend {name_a}: it leads by {delta} weighted points "
                f"({total_a} vs {total_b}) on this deterministic scorecard."
            )
        else:
            recommendation = name_b
            summary = (
                f"Recommend {name_b}: it leads by {delta} weighted points "
                f"({total_b} vs {total_a}) on this deterministic scorecard."
            )

        notes = [
            "Scores are deterministic heuristic estimates, not model-generated judgments.",
            "Use legal, brand, and market review before launch.",
        ]

        return CreativeScorecard(
            name_a=name_a,
            name_b=name_b,
            audience=audience or "Not specified",
            objective=objective or "Not specified",
            generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            criteria=comparisons,
            weighted_total_a=total_a,
            weighted_total_b=total_b,
            recommendation=recommendation,
            summary=summary,
            suggested_next_steps=self._next_steps(comparisons, name_a, name_b, recommendation),
            notes=notes,
        )

    def to_dict(self, scorecard: CreativeScorecard) -> dict[str, Any]:
        return asdict(scorecard)

    def to_json(self, scorecard: CreativeScorecard) -> str:
        return json.dumps(self.to_dict(scorecard), indent=2, ensure_ascii=False)

    def to_markdown(self, scorecard: CreativeScorecard) -> str:
        lines = [
            "# Creative Compare Scorecard",
            "",
            f"**{scorecard.name_a} total:** {scorecard.weighted_total_a}/10  ",
            f"**{scorecard.name_b} total:** {scorecard.weighted_total_b}/10  ",
            f"**Recommendation:** {scorecard.recommendation}",
            "",
            f"**Audience:** {scorecard.audience}",
            f"**Objective:** {scorecard.objective}",
            f"**Generated:** {scorecard.generated_at}",
            "",
            "## Summary",
            "",
            scorecard.summary,
            "",
            "## Scorecard",
            "",
            f"| Criterion | {scorecard.name_a} | {scorecard.name_b} | Winner | Rationale |",
            "|---|---:|---:|---|---|",
        ]
        for c in scorecard.criteria:
            rationale = f"A: {c.rationale_a} B: {c.rationale_b}"
            lines.append(
                f"| {c.label} | {c.score_a:.1f} | {c.score_b:.1f} | {c.winner} | {self._escape_md(rationale)} |"
            )
        lines.extend(["", "## Suggested next steps", ""])
        for step in scorecard.suggested_next_steps:
            lines.append(f"- {step}")
        lines.extend(["", "## Notes", ""])
        for note in scorecard.notes:
            lines.append(f"- {note}")
        return "\n".join(lines) + "\n"

    def _criterion(
        self,
        key: str,
        label: str,
        metrics_a: dict[str, Any],
        metrics_b: dict[str, Any],
    ) -> CriterionComparison:
        score_a, rationale_a = self._score(key, metrics_a)
        score_b, rationale_b = self._score(key, metrics_b)
        if abs(score_a - score_b) < 0.25:
            winner = "Tie"
        elif score_a > score_b:
            winner = "Creative A"
        else:
            winner = "Creative B"
        return CriterionComparison(
            criterion=key,
            label=label,
            score_a=score_a,
            score_b=score_b,
            winner=winner,
            rationale_a=rationale_a,
            rationale_b=rationale_b,
        )

    def _score(self, key: str, m: dict[str, Any]) -> tuple[float, str]:
        if key == "clarity":
            length_score = _closeness(m["avg_sentence_words"], ideal=15, tolerance=13)
            jargon_penalty = min(2.0, m["jargon_count"] * 0.5)
            score = 3.5 + 3.0 * length_score + 1.2 * m["specificity"] + 1.0 * m["has_plain_benefit"] - jargon_penalty
            rationale = (
                f"Avg sentence length {m['avg_sentence_words']:.1f}; "
                f"specificity {m['specificity']:.1f}; jargon terms {m['jargon_count']}."
            )
        elif key == "originality":
            cliche_penalty = min(3.0, m["cliche_count"] * 0.9)
            score = 4.5 + 1.8 * m["distinctive_ratio"] + 1.0 * m["specificity"] + 0.7 * m["metaphor_signal"] - cliche_penalty
            rationale = (
                f"Distinctive word ratio {m['distinctive_ratio']:.2f}; "
                f"cliché count {m['cliche_count']}; concrete detail signal {m['specificity']:.1f}."
            )
        elif key == "audience_fit":
            score = 3.0 + 4.0 * m["audience_overlap"] + 1.2 * m["second_person"] + 1.0 * m["problem_solution_signal"]
            rationale = (
                f"Audience keyword overlap {m['audience_overlap']:.2f}; "
                f"second-person/directness {m['second_person']:.1f}; problem-solution signal {m['problem_solution_signal']:.1f}."
            )
        elif key == "emotional_impact":
            score = 3.5 + 2.5 * min(1.0, m["emotion_count"] / 4) + 1.2 * m["sensory_signal"] + 1.0 * m["benefit_count_signal"]
            rationale = (
                f"Emotional words {m['emotion_count']}; sensory signal {m['sensory_signal']:.1f}; "
                f"benefit signal {m['benefit_count_signal']:.1f}."
            )
        elif key == "differentiation":
            score = 3.5 + 2.0 * min(1.0, m["differentiator_count"] / 3) + 1.5 * m["specificity"] + 1.0 * m["contrast_signal"]
            rationale = (
                f"Differentiator terms {m['differentiator_count']}; "
                f"contrast signal {m['contrast_signal']:.1f}; specificity {m['specificity']:.1f}."
            )
        elif key == "cta_strength":
            score = 2.5 + 3.0 * min(1.0, m["cta_count"] / 2) + 1.5 * m["urgency_signal"] + 1.0 * m["low_friction_signal"]
            rationale = (
                f"CTA terms {m['cta_count']}; urgency signal {m['urgency_signal']:.1f}; "
                f"low-friction signal {m['low_friction_signal']:.1f}."
            )
        elif key == "risk_safety":
            risk_penalty = min(5.5, m["risk_count"] * 1.0 + m["absolute_count"] * 0.5)
            score = 9.0 - risk_penalty
            risk_note = "; ".join(m["risk_notes"][:2]) if m["risk_notes"] else "No obvious high-risk terms found."
            rationale = f"Risk terms {m['risk_count']}; absolute claims {m['absolute_count']}. {risk_note}"
        else:
            raise KeyError(key)
        return round(_clamp(score, 0, 10), 1), rationale

    def _analyze(self, text: str, audience: str) -> dict[str, Any]:
        lowered = _normalize(text)
        words = _words(lowered)
        meaningful = [w for w in words if w not in STOPWORDS]
        sentences = [s.strip() for s in SENTENCE_RE.split(text) if s.strip()]
        sentence_lengths = [len(_words(s)) for s in sentences] or [len(words)]
        audience_words = {w for w in _words(_normalize(audience)) if w not in STOPWORDS and len(w) > 2}
        word_set = set(meaningful)
        audience_overlap = len(word_set & audience_words) / max(1, len(audience_words))

        cliche_count = sum(1 for phrase in CLICHES if phrase in lowered)
        risk_notes = [note for term, note in RISK_TERMS.items() if term in lowered]
        risk_count = len(risk_notes)
        absolute_count = sum(1 for term in ("always", "never", "everyone", "guaranteed", "perfect") if term in lowered)
        number_count = sum(1 for w in words if any(ch.isdigit() for ch in w))
        proof_count = sum(1 for w in words if w in {"because", "proof", "tested", "rated", "certified", "reviews", "data"})
        specificity = min(1.0, (number_count + proof_count + _concrete_phrase_count(lowered)) / 4)

        emotion_count = sum(1 for w in words if w in EMOTIONAL_WORDS)
        cta_count = sum(1 for w in words if w in CTA_WORDS)
        differentiator_count = sum(1 for w in words if w in DIFFERENTIATORS)
        jargon_count = sum(1 for w in words if w in JARGON)

        distinctive_ratio = len(set(meaningful)) / max(1, len(meaningful))
        second_person = 1.0 if any(w in words for w in ("you", "your", "yours")) else 0.0
        contrast_signal = 1.0 if any(p in lowered for p in ("without", "instead of", "unlike", "not just", "while others")) else 0.0
        urgency_signal = 1.0 if any(p in lowered for p in ("today", "now", "limited", "tonight", "this week", "before")) else 0.0
        low_friction_signal = 1.0 if any(p in lowered for p in ("free trial", "no commitment", "in minutes", "simple", "easy", "15 minutes", "15-minute")) else 0.0
        problem_solution_signal = 1.0 if any(p in lowered for p in ("so you can", "without", "tired of", "struggle", "problem", "because", "overloaded", "busy")) else 0.0
        has_plain_benefit = 1.0 if any(p in lowered for p in ("save", "helps", "so you can", "for", "without", "get")) else 0.0
        sensory_signal = 1.0 if any(w in words for w in ("fresh", "crisp", "warm", "bright", "smooth", "rich", "simple", "healthy")) else 0.0
        benefit_count_signal = min(1.0, sum(1 for w in words if w in {"save", "easy", "healthy", "faster", "better", "simple", "relief", "less", "more"}) / 3)
        metaphor_signal = 1.0 if any(p in lowered for p in ("like", "as if", "turns", "win back", "fuel", "spark")) else 0.0

        return {
            "avg_sentence_words": statistics.mean(sentence_lengths),
            "audience_overlap": audience_overlap,
            "cliche_count": cliche_count,
            "risk_count": risk_count,
            "risk_notes": risk_notes,
            "absolute_count": absolute_count,
            "specificity": specificity,
            "emotion_count": emotion_count,
            "cta_count": cta_count,
            "differentiator_count": differentiator_count,
            "jargon_count": jargon_count,
            "distinctive_ratio": distinctive_ratio,
            "second_person": second_person,
            "contrast_signal": contrast_signal,
            "urgency_signal": urgency_signal,
            "low_friction_signal": low_friction_signal,
            "problem_solution_signal": problem_solution_signal,
            "has_plain_benefit": has_plain_benefit,
            "sensory_signal": sensory_signal,
            "benefit_count_signal": benefit_count_signal,
            "metaphor_signal": metaphor_signal,
        }

    def _next_steps(
        self,
        comparisons: Iterable[CriterionComparison],
        name_a: str,
        name_b: str,
        recommendation: str,
    ) -> list[str]:
        weak = []
        for c in comparisons:
            if c.score_a < 6.0:
                weak.append(f"Improve {name_a}'s {c.label.lower()} ({c.score_a:.1f}/10).")
            if c.score_b < 6.0:
                weak.append(f"Improve {name_b}'s {c.label.lower()} ({c.score_b:.1f}/10).")
        steps = weak[:4]
        if recommendation != "Tie / needs human judgment":
            steps.insert(0, f"Use {recommendation} as the lead direction, then borrow any criterion wins from the other option.")
        else:
            steps.insert(0, "Create a hybrid option using the highest-scoring criteria from both concepts.")
        steps.append("Validate with target-audience feedback and brand/legal review before launch.")
        return steps

    @staticmethod
    def _escape_md(value: str) -> str:
        return value.replace("|", "\\|").replace("\n", " ")


def _normalize(text: str) -> str:
    return text.lower().replace("’", "'").replace("–", "-").replace("—", "-")


def _words(text: str) -> list[str]:
    return [m.group(0).lower() for m in WORD_RE.finditer(text)]


def _concrete_phrase_count(text: str) -> int:
    phrases = ("minutes", "hours", "days", "percent", "%", "$", "family", "weeknight", "trial", "recipe", "reviews")
    return sum(1 for p in phrases if p in text)


def _closeness(value: float, *, ideal: float, tolerance: float) -> float:
    return _clamp(1 - abs(value - ideal) / tolerance, 0, 1)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
