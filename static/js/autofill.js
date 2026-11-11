/*
 * Decides what a school ID read may write into the registration form.
 * Pure logic with no page access, so it can be tested on its own (jstests/autofill.test.js).
 *
 * The rules: only fields the server classifies as ID-derived can ever be written or cleared;
 * a value the person typed or corrected is never overwritten without asking; each field is
 * handled on its own, so fixing the last name never touches the first name.
 */
(function (root) {
  "use strict";

  const EMPTY = ["", "null", "undefined", "n/a"];

  function idFieldNames(schema) {
    return schema.filter((field) => field.source === "id").map((field) => field.name);
  }

  // The text read for one field, or "" when nothing usable was read.
  function readValue(extracted, name) {
    const item = extracted ? extracted[name] : undefined;
    const value = item !== null && typeof item === "object" ? item.value : item;
    const text = value === undefined || value === null ? "" : String(value).trim();
    return EMPTY.includes(text.toLowerCase()) ? "" : text;
  }

  /*
   * schema:     the field list from core/registration_fields.py
   * values:     what is in the form now               {name: text}
   * autofilled: what autofill last wrote              {name: text}
   * extracted:  the new ID read                       {name: {value, confidence}}
   * replace:    the person chose "Replace my edits"
   *
   * Returns {writes: {name: text}, clears: [name], conflicts: [name]}.
   * conflicts lists fields the person edited whose new ID value differs: ask before replacing.
   */
  function plan(schema, values, autofilled, extracted, replace) {
    const result = { writes: {}, clears: [], conflicts: [] };
    idFieldNames(schema).forEach((name) => {
      const current = values[name] || "";
      const fresh = readValue(extracted, name);
      const untouched = Object.prototype.hasOwnProperty.call(autofilled, name) && current === autofilled[name];
      const edited = current !== "" && !untouched;
      if (fresh) {
        if (!edited) result.writes[name] = fresh;
        else if (fresh !== current) {
          result.conflicts.push(name);
          if (replace) result.writes[name] = fresh;
        }
      } else if (untouched && current !== "") {
        result.clears.push(name); // left over from an earlier ID, and not something the person typed
      }
    });
    return result;
  }

  // Apply a plan to plain copies of the form state. Returns the new {values, autofilled}.
  function apply(values, autofilled, result) {
    const next = { values: Object.assign({}, values), autofilled: Object.assign({}, autofilled) };
    Object.keys(result.writes).forEach((name) => {
      next.values[name] = result.writes[name];
      next.autofilled[name] = result.writes[name];
    });
    result.clears.forEach((name) => {
      next.values[name] = "";
      delete next.autofilled[name];
    });
    return next;
  }

  // "read" = still exactly what the ID said, "edited" = read then changed, "typed" = never autofilled.
  function origin(name, value, autofilled) {
    if (!Object.prototype.hasOwnProperty.call(autofilled, name)) return "typed";
    return value === autofilled[name] ? "read" : "edited";
  }

  const api = { idFieldNames, readValue, plan, apply, origin };
  if (typeof module === "object" && module.exports) module.exports = api;
  else (root.REUSE = root.REUSE || {}).autofill = api;
})(typeof window !== "undefined" ? window : globalThis);
