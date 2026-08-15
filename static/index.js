(function () {
    "use strict";

    // --- Empty link prevention ---
    document.querySelectorAll('a[href="#"]').forEach(function (link) {
        link.addEventListener("click", function (e) {
            e.preventDefault();
        });
    });

    var yearSelect = document.getElementById("year-level-select");
    var semesterSelect = document.getElementById("semester-select");
    var majorSelect = document.getElementById("major-select");
    var majorField = document.getElementById("major-field");
    var coursePanel = document.getElementById("course-preview-panel");
    var courseHeader = document.getElementById("course-preview-header");
    var courseList = document.getElementById("course-preview-list");
    var courseHelp = document.getElementById("course-selection-help");

    var activeFetchController = null;
    var activeFetchId = 0;

    var yearLabels = {
        "1": "1st Year",
        "2": "2nd Year",
        "3": "3rd Year",
        "4": "4th Year",
    };

    function isMajorRequired(yearValue, semesterValue) {
        if ((yearValue === "3" || yearValue === "3rd Year") &&
            (semesterValue === "2nd Semester" || semesterValue === "2nd" || semesterValue === "2")) {
            return true;
        }
        if ((yearValue === "4" || yearValue === "4th Year") &&
            (semesterValue === "1st Semester" || semesterValue === "1st" || semesterValue === "1")) {
            return true;
        }
        if ((yearValue === "4" || yearValue === "4th Year") &&
            (semesterValue === "2nd Semester" || semesterValue === "2nd" || semesterValue === "2")) {
            return true;
        }
        return false;
    }

    function getYearLabel(yearValue) {
        return yearLabels[yearValue] || ("Year " + yearValue);
    }

    function getSelectionHeaderText() {
        if (!semesterSelect || !semesterSelect.value) {
            return "";
        }
        if (yearSelect && yearSelect.value) {
            var header = getYearLabel(yearSelect.value) + " - " + semesterSelect.value;
            if (isMajorRequired(yearSelect.value, semesterSelect.value) && majorSelect && majorSelect.value) {
                header += " (" + majorSelect.value + ")";
            }
            return header;
        }
        return semesterSelect.value + " (All Year Levels)";
    }

    function toggleMajorField() {
        if (!majorField) return;
        var selectedYear = yearSelect ? yearSelect.value : "";
        var selectedSemester = semesterSelect ? semesterSelect.value : "";
        var showMajor = selectedYear ? isMajorRequired(selectedYear, selectedSemester) : false;

        if (showMajor) {
            majorField.style.display = "";
        } else {
            majorField.style.display = "none";
            if (majorSelect) {
                majorSelect.value = "";
            }
        }
    }

    function canFetchCourses() {
        if (!yearSelect || !yearSelect.value || !semesterSelect || !semesterSelect.value) {
            return false;
        }
        if (isMajorRequired(yearSelect.value, semesterSelect.value)) {
            return Boolean(majorSelect && majorSelect.value);
        }
        return true;
    }

    function updateCourseHeader() {
        if (!courseHeader) {
            return;
        }
        courseHeader.textContent = getSelectionHeaderText();
    }

    function hideCoursePanel() {
        if (coursePanel) {
            coursePanel.style.display = "none";
        }
        if (courseHeader) {
            courseHeader.textContent = "";
        }
        if (courseList) {
            courseList.innerHTML = "";
        }
        if (courseHelp) {
            courseHelp.textContent = "";
        }
    }

    function renderCourses(courses) {
        if (!courseList) {
            return;
        }

        if (!courses.length) {
            var selectedYear = yearSelect ? yearSelect.value : "";
            var selectedSemester = semesterSelect ? semesterSelect.value : "";
            if (isMajorRequired(selectedYear, selectedSemester)) {
                courseList.innerHTML =
                    '<p class="text-muted text-center mb-0">Please select a Major to view courses.</p>';
            } else {
                courseList.innerHTML =
                    '<p class="text-muted text-center mb-0">No courses found for this Year Level and Semester.</p>';
            }
            return;
        }

        var html = "";
        courses.forEach(function (course) {
            html +=
                '<div class="form-check">' +
                '<input class="form-check-input" type="checkbox" name="course_ids" value="' + course.course_id + '" id="course_' + course.course_id + '" checked>' +
                '<label class="form-check-label" for="course_' + course.course_id + '">' +
                (course.course_name || "Untitled Course") +
                (course.program ? '<small class="text-muted d-block">' + course.program + "</small>" : "") +
                "</label>" +
                "</div>";
        });

        courseList.innerHTML = html;
    }

    function fetchCourses() {
        if (!yearSelect) return;
        toggleMajorField();

        if (!canFetchCourses()) {
            if (activeFetchController) {
                activeFetchController.abort();
                activeFetchController = null;
            }
            hideCoursePanel();
            return;
        }

        if (coursePanel) {
            coursePanel.style.display = "";
        }

        updateCourseHeader();

        activeFetchId += 1;
        var fetchId = activeFetchId;

        if (activeFetchController) {
            activeFetchController.abort();
        }
        activeFetchController = new AbortController();

        if (courseList) {
            courseList.innerHTML =
                '<div class="text-muted small"><span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Loading courses...</div>';
        }
        if (courseHelp) {
            courseHelp.textContent = "";
        }

        var url =
            "/api/courses?year_level=" + encodeURIComponent(yearSelect.value) +
            "&semester=" + encodeURIComponent(semesterSelect.value);

        if (isMajorRequired(yearSelect.value, semesterSelect.value) && majorSelect && majorSelect.value) {
            url += "&major=" + encodeURIComponent(majorSelect.value);
        }

        fetch(url, {
            method: "GET",
            headers: { "X-Requested-With": "XMLHttpRequest" },
            signal: activeFetchController.signal,
        })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error("Unable to load courses.");
                }
                return response.json();
            })
            .then(function (data) {
                if (fetchId !== activeFetchId) {
                    return;
                }
                renderCourses(data.courses || []);
            })
            .catch(function (err) {
                if (err.name === "AbortError") {
                    return;
                }
                if (fetchId === activeFetchId && courseList) {
                    courseList.innerHTML =
                        '<p class="text-muted text-center mb-0">Unable to load courses right now.</p>';
                }
            })
            .finally(function () {
                if (fetchId === activeFetchId) {
                    activeFetchController = null;
                }
            });
    }

    if (yearSelect) {
        yearSelect.addEventListener("change", fetchCourses);
    }
    if (semesterSelect && yearSelect) {
        semesterSelect.addEventListener("change", fetchCourses);
    }
    if (majorSelect && yearSelect) {
        majorSelect.addEventListener("change", fetchCourses);
    }

    if (yearSelect) {
        toggleMajorField();
        hideCoursePanel();
    }

    // --- Schedule form validation (only when yearSelect or course panel exists) ---
    var scheduleForm = document.getElementById("schedule-form");
    if (scheduleForm && yearSelect) {
        scheduleForm.addEventListener("submit", function (e) {
            var y = yearSelect.value;
            var s = semesterSelect ? semesterSelect.value : "";

            if (!y) {
                e.preventDefault();
                alert("Please select a Year Level first.");
                return;
            }

            if (!s) {
                e.preventDefault();
                alert("Please select a Semester first.");
                return;
            }

            if (isMajorRequired(y, s)) {
                var major = majorSelect ? majorSelect.value : "";
                if (!major) {
                    e.preventDefault();
                    alert("Please select a Major for this schedule.");
                    return;
                }
            }

            var checked = scheduleForm.querySelectorAll(
                'input[name="course_ids"]:checked'
            ).length;

            if (courseList && checked === 0) {
                if (courseHelp) {
                    courseHelp.textContent =
                        "Please select at least one course to generate a schedule.";
                }
                e.preventDefault();
                return;
            }
        });
    }

    // --- Global Custom Delete Confirmation ---
    document.addEventListener("click", function (e) {
        var confirmEl = e.target.closest("[data-confirm]");
        if (!confirmEl) return;

        e.preventDefault();
        e.stopPropagation();

        var message = confirmEl.getAttribute("data-confirm") || "Are you sure you want to delete this item?";
        var title = confirmEl.getAttribute("data-title") || confirmEl.getAttribute("title") || "";

        if (!title) {
            var msgLower = message.toLowerCase();
            if (msgLower.includes("course")) title = "Delete Course";
            else if (msgLower.includes("professor")) title = "Delete Professor";
            else if (msgLower.includes("room")) title = "Delete Room";
            else if (msgLower.includes("timeslot")) title = "Delete Timeslot";
            else if (msgLower.includes("schedule")) title = "Delete Schedule";
            else if (msgLower.includes("user")) title = "Delete User";
            else title = "Delete Item";
        }

        var confirmText = confirmEl.getAttribute("data-confirm-text") || "Delete";
        var subtext = confirmEl.getAttribute("data-subtext") || "This action cannot be undone.";

        if (typeof window.showConfirmModal === "function") {
            window.showConfirmModal({
                title: title,
                message: message,
                subtext: subtext,
                confirmText: confirmText,
                confirmBtnClass: "btn-danger",
                onConfirm: function () {
                    if (confirmEl.tagName === "A" && confirmEl.href) {
                        window.location.href = confirmEl.href;
                    } else if (confirmEl.form) {
                        confirmEl.form.submit();
                    } else if (confirmEl.tagName === "BUTTON" && confirmEl.type === "submit") {
                        var form = confirmEl.closest("form");
                        if (form) form.submit();
                    }
                }
            });
        }
    }, true);
})();
