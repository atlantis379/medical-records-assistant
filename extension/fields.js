// Routes dictated text into the matching record field (主诉, 现病史 ...).
//
// The draft stays a plain text with headings ("主诉：…"), so copy, export, versions and the review
// panel are unchanged. Routing finds the heading and inserts the dictation under it.
//
// Pure functions only: the same file runs in the browser (window.FieldRouter) and under Node for tests.
(function (root) {
  "use strict";

  // `weak` aliases are common words ("诊断") and only count as a command when punctuation follows.
  // Aliases are provisional: extend them from the evaluation's field_command results.
  const FIELD_DEFS = [
    { key: "chief_complaint", label: "主诉", labelEn: "Chief complaint", aliases: ["主述"], order: 10 },
    { key: "present_illness", label: "现病史", labelEn: "Present illness", aliases: ["现病时", "现病事"], order: 20 },
    { key: "epidemiology", label: "流行病学史", labelEn: "Epidemiology", aliases: [], order: 30, specialties: ["infectious_disease"] },
    { key: "past_history", label: "既往史", labelEn: "Past history", aliases: ["既往时"], order: 40 },
    { key: "personal_history", label: "个人史", labelEn: "Personal history", aliases: [], order: 50 },
    { key: "family_history", label: "家族史", labelEn: "Family history", aliases: [], order: 60 },
    { key: "physical_exam", label: "体格检查", labelEn: "Physical exam", aliases: ["查体"], order: 70 },
    { key: "specialty_exam", label: "专科检查", labelEn: "Specialty exam", aliases: [], order: 75, specialties: ["orthopedics"] },
    { key: "auxiliary_exam", label: "辅助检查", labelEn: "Investigations", aliases: [], order: 80 },
    { key: "diagnosis", label: "初步诊断", labelEn: "Diagnosis", aliases: [], weak: ["诊断"], order: 90 },
    { key: "course", label: "诊疗经过", labelEn: "Course and treatment", aliases: [], order: 100 }
  ];

  const BOUNDARY = "(?:^|(?<=[。；;！!？?\\n，,、]))";
  const TRAILING_PUNCT = "[：:，,、]?[ \\t]*";

  function escapeRegExp(text) {
    return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  // Fields available to a doctor: the common ones plus those tied to their specialties.
  function activeDefs(specialties) {
    const chosen = specialties || [];
    return FIELD_DEFS
      .filter(def => !def.specialties || def.specialties.some(item => chosen.includes(item)))
      .sort((a, b) => a.order - b.order);
  }

  // ---------------------------------------------------------------- spoken commands
  // A command is a field name at the start of the text or right after a pause (sentence or comma).
  function findCommands(text, defs) {
    const found = [];
    for (const def of defs) {
      const names = [def.label, ...(def.aliases || [])].map(name => ({ name, weak: false }))
        .concat((def.weak || []).map(name => ({ name, weak: true })));
      for (const { name, weak } of names) {
        const pattern = new RegExp(`${BOUNDARY}[ \\t]*(${escapeRegExp(name)})${weak ? "(?=[：:，,])" : ""}${TRAILING_PUNCT}`, "g");
        for (const match of text.matchAll(pattern)) {
          const labelStart = match.index + match[0].indexOf(match[1]);
          found.push({ index: match.index, labelStart, end: match.index + match[0].length, def });
        }
      }
    }
    found.sort((a, b) => a.labelStart - b.labelStart || (b.end - b.labelStart) - (a.end - a.labelStart));
    const result = [];
    let cursor = -1;
    for (const item of found) {          // drop overlaps; the longer match at the same place sorts first
      if (item.labelStart >= cursor) {
        result.push(item);
        cursor = item.end;
      }
    }
    return result;
  }

  // Splits dictated text by the field commands it contains.
  // `current` is the field in force when the text starts (null = none chosen).
  function route(text, current, defs) {
    const commands = findCommands(text, defs);
    const segments = [];
    if (!commands.length) {
      return { segments: [{ key: current, text }], current, commandCount: 0 };
    }
    // the pause comma just before the next field name closes the sentence of the previous field
    const closeSentence = piece => piece.replace(/[，,、][ \t]*$/, "。");
    const first = closeSentence(text.slice(0, commands[0].index).replace(/^[\s，,、]+/, ""));
    if (first.trim()) segments.push({ key: current, text: first });
    commands.forEach((command, i) => {
      const last = i + 1 >= commands.length;
      const piece = text.slice(command.end, last ? text.length : commands[i + 1].index);
      segments.push({ key: command.def.key, text: last ? piece : closeSentence(piece) });
    });
    return { segments, current: commands[commands.length - 1].def.key, commandCount: commands.length };
  }

  // ---------------------------------------------------------------- the draft's headings
  function locateSections(draft, defs) {
    const sections = [];
    for (const def of defs) {
      const pattern = new RegExp(`(^|\\n)[ \\t]*(${escapeRegExp(def.label)})[：:][ \\t]*`, "g");
      for (const match of draft.matchAll(pattern)) {
        sections.push({ def, headStart: match.index + match[1].length, bodyStart: match.index + match[0].length });
      }
    }
    sections.sort((a, b) => a.headStart - b.headStart);
    sections.forEach((section, i) => { section.end = i + 1 < sections.length ? sections[i + 1].headStart : draft.length; });
    return sections;
  }

  function joinParagraph(before, addition) {
    if (!before) return addition;
    return before.endsWith("\n") ? before + addition : `${before}\n${addition}`;
  }

  // Inserts `content` at the end of a field, creating the heading in its usual place if needed.
  function insertIntoField(draft, def, content, defs) {
    const sections = locateSections(draft, defs);
    const existing = sections.filter(section => section.def.key === def.key).pop();
    if (existing) {
      const before = draft.slice(0, existing.bodyStart);
      const body = draft.slice(existing.bodyStart, existing.end);
      const after = draft.slice(existing.end);
      const trimmed = body.replace(/\s+$/, "");
      const trailing = body.slice(trimmed.length) || (after ? "\n" : "");
      return before + trimmed + content + trailing + after;
    }
    const heading = `${def.label}：${content}`;
    const next = sections.find(section => section.def.order > def.order);
    if (next) return draft.slice(0, next.headStart) + heading + "\n" + draft.slice(next.headStart);
    return joinParagraph(draft, heading);
  }

  // Dictation that belongs to no field goes above the first heading (the "unassigned" area).
  function insertUnassigned(draft, content, defs) {
    const sections = locateSections(draft, defs);
    if (!sections.length) return joinParagraph(draft, content);
    const head = sections[0].headStart;
    const above = draft.slice(0, head).replace(/\s+$/, "");
    return (above ? above + content : content) + "\n" + draft.slice(head);
  }

  // Routes `text` into the draft. `routed` is false when nothing was assigned to a field, so the
  // caller can keep its ordinary insertion behaviour.
  function apply(draft, text, state, defs) {
    const result = route(text, state.current, defs);
    const byKey = Object.fromEntries(defs.map(def => [def.key, def]));
    const assigned = result.segments.some(segment => segment.key && byKey[segment.key]);
    if (!assigned) return { text: draft, current: result.current, routed: false };
    let out = draft;
    for (const segment of result.segments) {
      if (segment.key && byKey[segment.key]) out = insertIntoField(out, byKey[segment.key], segment.text, defs);
      else if (segment.text.trim()) out = insertUnassigned(out, segment.text, defs);
    }
    return { text: out, current: result.current, routed: true };
  }

  // ---------------------------------------------------------------- reading the draft
  function fieldAt(draft, caret, defs) {
    const section = locateSections(draft, defs).find(item => caret >= item.headStart && caret <= item.end);
    return section ? section.def.key : null;
  }

  function fieldStatus(draft, defs) {
    const status = {};
    for (const section of locateSections(draft, defs)) {
      const body = draft.slice(section.bodyStart, section.end).trim();
      status[section.def.key] = { present: true, empty: body === "" };
    }
    return status;
  }

  // Text above the first heading, when headings exist: content that was never put in a field.
  function unassignedText(draft, defs) {
    const sections = locateSections(draft, defs);
    return sections.length ? draft.slice(0, sections[0].headStart).trim() : "";
  }

  function emptyFields(draft, defs) {
    return locateSections(draft, defs)
      .filter(section => draft.slice(section.bodyStart, section.end).trim() === "")
      .map(section => section.def.label);
  }

  const api = { FIELD_DEFS, activeDefs, findCommands, route, locateSections, insertIntoField, insertUnassigned, apply,
                fieldAt, fieldStatus, unassignedText, emptyFields };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.FieldRouter = api;
})(typeof window !== "undefined" ? window : globalThis);
