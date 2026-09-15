"""Conservative, local-only location proposals from saved transcription interpretations."""

import re

from .locations import matches, norm
from .models import InterpretationLocationOption, Profile, Run, Status
from .regions import in_region, region_conflicts


def location_options(run: Run, profile: Profile) -> list[InterpretationLocationOption]:
    options = []
    for task in run.tasks.values():
        if (
            task.origin != "ai"
            or task.status not in {Status.candidate, Status.unhandled}
            or task.location.status == "confirmed"
            or not task.place
            or region_conflicts(run.region, task.place.municipality)
        ):
            continue
        for comm in run.communications.values():
            if (
                not comm.extraction
                or comm.extraction_status != "done"
                or comm.extraction_revision != comm.revision
                or not any(
                    e.communication_id == comm.id and e.revision == comm.revision
                    for e in task.evidence
                )
                or not any(original.place == task.place for original in comm.extraction.tasks)
            ):
                continue
            for index, interpretation in enumerate(comm.extraction.transcript_interpretations):
                evidence = interpretation.evidence
                meaning = interpretation.possible_meaning
                if (
                    not meaning
                    or evidence.communication_id != comm.id
                    or evidence.revision != comm.revision
                    or evidence.quote not in comm.text
                    or norm(task.place.expression) not in norm(evidence.quote)
                    or not any(
                        e.communication_id == comm.id
                        and e.revision == comm.revision
                        and (e.quote in evidence.quote or evidence.quote in e.quote)
                        for e in task.evidence
                    )
                ):
                    continue
                # A whole-communication citation may cover multiple places. Do not attach
                # one place's interpretation to another task sharing that broad citation.
                expressions = {
                    norm(t.place.expression)
                    for t in comm.extraction.tasks
                    if t.place and norm(t.place.expression) in norm(evidence.quote)
                }
                if expressions != {norm(task.place.expression)}:
                    continue
                # Keep obvious detail suffixes even if extraction omitted the detail field.
                suffix = re.search(
                    r"(入口|出口|[東西南北]口|[東西南北正裏]門|駐車場|付近|橋上|側|前|裏|横)$",
                    norm(task.place.expression),
                )
                if suffix and not norm(meaning).endswith(suffix.group()):
                    continue
                interpreted_place = task.place.model_copy(
                    update={"expression": meaning, "search_name": meaning}
                )
                candidates = [
                    p
                    for p in profile.locations
                    if in_region(run.region, p.municipality) and matches(interpreted_place, p)
                ]
                if len(candidates) == 1:
                    options.append(
                        InterpretationLocationOption(
                            communication_id=comm.id,
                            revision=comm.revision,
                            interpretation_index=index,
                            task_id=task.id,
                            task_version=task.version,
                            location=candidates[0],
                        )
                    )
    return options
