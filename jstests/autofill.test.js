// Run with:  node --test jstests/
const test = require("node:test");
const assert = require("node:assert/strict");
const { plan, apply, origin, readValue } = require("../static/js/autofill.js");

const schema = [
  { name: "last_name", source: "id" }, { name: "first_name", source: "id" }, { name: "middle_name", source: "id" },
  { name: "suffix", source: "id" }, { name: "student_id_number", source: "id" }, { name: "program", source: "id" },
  { name: "email", source: "manual" }, { name: "mobile", source: "manual" }, { name: "user_type", source: "manual" },
  { name: "password", source: "manual" }, { name: "confirm_password", source: "manual" }, { name: "consent", source: "manual" },
];
const read = (fields) => Object.fromEntries(Object.entries(fields).map(([k, v]) => [k, { value: v, confidence: 0.9 }]));
const MANUAL = ["email", "mobile", "user_type", "password", "confirm_password", "consent"];

test("the first read fills only ID-derived fields", () => {
  const extracted = read({
    last_name: "Dela Cruz", first_name: "Juan", student_id_number: "21-12345",
    email: "juan@g.batstate-u.edu.ph", mobile: "09171234567", user_type: "student", password: "x", consent: true,
  });
  const result = plan(schema, {}, {}, extracted, false);
  assert.deepEqual(result.writes, { last_name: "Dela Cruz", first_name: "Juan", student_id_number: "21-12345" });
  assert.deepEqual(result.clears, []);
  MANUAL.forEach((name) => assert.ok(!(name in result.writes), name));
});

test("manual fields are never written or cleared, even with 'replace'", () => {
  const values = { email: "me@g.batstate-u.edu.ph", mobile: "9171234567", user_type: "faculty", password: "Secret@1" };
  const extracted = read({ email: "other@x.ph", mobile: "000", user_type: "student", password: "hacked", first_name: "Ana" });
  for (const replace of [false, true]) {
    const result = plan(schema, values, {}, extracted, replace);
    const next = apply(values, {}, result);
    MANUAL.forEach((name) => assert.equal(next.values[name], values[name], name));
    assert.deepEqual(result.clears, []);
  }
});

test("a missing value is left blank, never a placeholder", () => {
  const extracted = { first_name: { value: "Juan" }, middle_name: { value: "" }, suffix: { value: "N/A" }, program: null, last_name: { value: "null" } };
  const result = plan(schema, {}, {}, extracted, false);
  assert.deepEqual(result.writes, { first_name: "Juan" });
  for (const junk of ["", "null", "undefined", "N/A", "  ", null, undefined]) assert.equal(readValue({ x: junk }, "x"), "");
});

test("fixing one name part leaves the others alone", () => {
  let state = apply({}, {}, plan(schema, {}, {}, read({ last_name: "Cruz", first_name: "Dela", middle_name: "Juan" }), false));
  state.values.last_name = "Dela Cruz"; // the person corrects a bad split
  const again = plan(schema, state.values, state.autofilled, read({ last_name: "Cruz", first_name: "Dela", middle_name: "Juan" }), false);
  assert.deepEqual(again.conflicts, ["last_name"]);
  const kept = apply(state.values, state.autofilled, again);
  assert.equal(kept.values.last_name, "Dela Cruz");
  assert.equal(kept.values.first_name, "Dela");
  assert.equal(kept.values.middle_name, "Juan");
});

test("a different ID asks before replacing edits", () => {
  const first = apply({}, {}, plan(schema, {}, {}, read({ last_name: "Cruz", first_name: "Juan", program: "BSIT" }), false));
  first.values.first_name = "Juanito";           // edited after autofill
  first.values.student_id_number = "21-00001";   // typed: the first ID had no number
  const other = read({ last_name: "Reyes", first_name: "Ana", student_id_number: "22-99999" });

  const keep = plan(schema, first.values, first.autofilled, other, false);
  assert.deepEqual(keep.conflicts.sort(), ["first_name", "student_id_number"]);
  const kept = apply(first.values, first.autofilled, keep).values;
  assert.equal(kept.first_name, "Juanito");
  assert.equal(kept.student_id_number, "21-00001");
  assert.equal(kept.last_name, "Reyes");  // untouched autofill follows the new ID
  assert.equal(kept.program, "");         // came from the first ID and isn't on the new one

  const replaced = apply(first.values, first.autofilled, plan(schema, first.values, first.autofilled, other, true)).values;
  assert.equal(replaced.first_name, "Ana");
  assert.equal(replaced.student_id_number, "22-99999");
});

test("the same ID again causes no conflict and no change", () => {
  const extracted = read({ last_name: "Cruz", first_name: "Juan" });
  const state = apply({}, {}, plan(schema, {}, {}, extracted, false));
  const again = plan(schema, state.values, state.autofilled, extracted, false);
  assert.deepEqual(again.conflicts, []);
  assert.deepEqual(apply(state.values, state.autofilled, again).values, state.values);
});

test("a typed value survives an ID that has nothing for that field", () => {
  const values = { program: "BS Computer Science" };
  const result = plan(schema, values, {}, read({ first_name: "Juan" }), true);
  assert.deepEqual(result.clears, []);
  assert.equal(apply(values, {}, result).values.program, "BS Computer Science");
});

test("origin says where a value came from", () => {
  const autofilled = { first_name: "Juan", last_name: "Cruz" };
  assert.equal(origin("first_name", "Juan", autofilled), "read");
  assert.equal(origin("last_name", "Dela Cruz", autofilled), "edited");
  assert.equal(origin("program", "BSIT", autofilled), "typed");
});
