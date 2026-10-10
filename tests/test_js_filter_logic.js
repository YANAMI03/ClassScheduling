// Test Professor Load Live Filters JavaScript logic directly in Node.js
const assert = require('assert');

// 1. checkProfessorNameMatch implementation from professor_load.html
function checkProfessorNameMatch(profName, searchStr) {
    if (!searchStr) return true;
    var cleanQuery = searchStr.trim().toLowerCase().replace(/\s+/g, ' ');
    if (!cleanQuery) return true;

    var cleanName = (profName || '').toLowerCase().replace(/\s+/g, ' ');

    // Direct phrase match
    if (cleanName.indexOf(cleanQuery) !== -1) {
        return true;
    }

    // Space-separated token matching (e.g. "Turing Alan")
    var tokens = cleanQuery.split(' ').filter(Boolean);
    if (tokens.length > 1) {
        return tokens.every(function(t) {
            return cleanName.indexOf(t) !== -1;
        });
    }
    return false;
}

// 2. checkCourseMatch implementation from professor_load.html
function checkCourseMatch(rowCoursesStr, selectedCourse) {
    if (!selectedCourse) return true;
    if (!rowCoursesStr) return false;
    var list = rowCoursesStr.split('|').map(function(s) { return s.trim(); });
    var target = selectedCourse.trim().toLowerCase();
    var targetNorm = target.replace(/[\s\-_]/g, '');
    return list.some(function(c) {
        var cLower = c.toLowerCase();
        return cLower === target || cLower.replace(/[\s\-_]/g, '') === targetNorm;
    });
}

console.log('--- Step 3: Running JS Live Filter Verification Tests ---');

// Test 1: Search by full name, partial name, and casing, extra spaces
console.log('Test 1: Search by full name, partial name, and casing...');
assert.strictEqual(checkProfessorNameMatch('alan turing', 'Alan'), true, 'Partial match (cased)');
assert.strictEqual(checkProfessorNameMatch('alan turing', 'turing'), true, 'Last name match');
assert.strictEqual(checkProfessorNameMatch('alan turing', 'ALAN TURING'), true, 'Full uppercase match');
assert.strictEqual(checkProfessorNameMatch('alan turing', '   alan    tur   '), true, 'Extra spaces match');
assert.strictEqual(checkProfessorNameMatch('alan turing', 'Turing Alan'), true, 'Reversed tokens match');
assert.strictEqual(checkProfessorNameMatch('alan turing', 'ada'), false, 'Non-matching name');
console.log('✓ Test 1 passed.');

// Test 2: Filter by a course that has multiple professors, and by one that has a single professor
console.log('Test 2: Course filter matching (multi-prof and single-prof)...');
const professors = [
    { id: 1, name: 'alan turing', courses: 'IT-PF02' },
    { id: 2, name: 'ada lovelace', courses: 'IT-PF02|CC-104' },
    { id: 3, name: 'grace hopper', courses: 'CC-104' },
    { id: 4, name: 'john von neumann', courses: 'IT-NET01' }
];

// Multi-professor course: IT-PF02 (Prof 1 and 2)
const itPf02Matches = professors.filter(p => checkCourseMatch(p.courses, 'IT-PF02'));
assert.strictEqual(itPf02Matches.length, 2);
assert.deepStrictEqual(itPf02Matches.map(p => p.id), [1, 2]);

// Single-professor course: IT-NET01 (Prof 4 only)
const itNetMatches = professors.filter(p => checkCourseMatch(p.courses, 'IT-NET01'));
assert.strictEqual(itNetMatches.length, 1);
assert.deepStrictEqual(itNetMatches.map(p => p.id), [4]);

// All Courses ("") matches all
const allCoursesMatches = professors.filter(p => checkCourseMatch(p.courses, ''));
assert.strictEqual(allCoursesMatches.length, 4);
console.log('✓ Test 2 passed.');

// Test 3: Combine search and course filters with AND behavior, count badge, empty state
console.log('Test 3: Combined AND filters, count badge, and empty state...');
function applyCombinedFilter(list, search, course) {
    return list.filter(p =>
        checkProfessorNameMatch(p.name, search) &&
        checkCourseMatch(p.courses, course)
    );
}

// Search "Ada" + Course "CC-104" -> 1 match (Ada Lovelace)
let res = applyCombinedFilter(professors, 'Ada', 'CC-104');
assert.strictEqual(res.length, 1);
assert.strictEqual(res[0].name, 'ada lovelace');
let badgeText = `${res.length} of ${professors.length} professors`;
assert.strictEqual(badgeText, '1 of 4 professors');

// Search "Ada" + Course "IT-NET01" -> 0 matches (Empty state!)
res = applyCombinedFilter(professors, 'Ada', 'IT-NET01');
assert.strictEqual(res.length, 0);
let isEmptyState = (res.length === 0 && professors.length > 0);
assert.strictEqual(isEmptyState, true);
badgeText = `${res.length} of ${professors.length} professors`;
assert.strictEqual(badgeText, '0 of 4 professors');
console.log('✓ Test 3 passed.');

// Test 4: Clear filters returns full list and original count
console.log('Test 4: Clear filters...');
res = applyCombinedFilter(professors, '', '');
assert.strictEqual(res.length, professors.length);
badgeText = `${professors.length} professors assigned`;
assert.strictEqual(badgeText, '4 professors assigned');
console.log('✓ Test 4 passed.');

// Test 6: Course dropdown unique sorted population including master catalog (e.g. CC-100)
console.log('Test 6: Unique sorted assigned courses dropdown population...');
const allCatalogCourses = [{ course_code: 'CC-100' }, { course_code: 'CC-101' }, { course_code: 'CC-102' }];
const courseSet = new Set();
// Add catalog courses
allCatalogCourses.forEach(c => courseSet.add(c.course_code));
// Add assigned courses
professors.forEach(p => {
    p.courses.split('|').forEach(c => { if (c.trim()) courseSet.add(c.trim()); });
});
const sortedCourses = Array.from(courseSet).sort((a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' }));
assert.ok(sortedCourses.includes('CC-100'), 'CC-100 must be in dropdown list');
assert.strictEqual(sortedCourses[0], 'CC-100', 'CC-100 must be sorted at the beginning of CC courses');
console.log('✓ Test 6 passed.');

// Test 7: Selecting CC-100 (unassigned course) correctly shows empty state
console.log('Test 7: Selecting unassigned course (CC-100)...');
let cc100Matches = applyCombinedFilter(professors, '', 'CC-100', '');
assert.strictEqual(cc100Matches.length, 0);
assert.strictEqual(checkCourseMatch("CC-100", "cc100"), true, "Flexible hyphen match");
assert.strictEqual(checkCourseMatch("CC100", "CC-100"), true, "Flexible hyphen match");
console.log('✓ Test 7 passed.');

console.log('ALL STEP 3 JS CHECKS PASSED SUCCESSFULLY!');
