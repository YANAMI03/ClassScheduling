(function () {
    "use strict";

    /* =========================================================================
     * DEVELOPER GUIDE: How to add loading states to buttons
     * -------------------------------------------------------------------------
     * 1. Forms (Class A): Any standard form submit button (<button type="submit">)
     *    automatically shows an inline spinner, size lock, and gerund text (e.g. "Save" -> "Saving...").
     *    Custom label can be specified with: data-loading-text="Custom..."
     * 2. Navigation / Download Links (Class A): Any <a> styled with .btn automatically
     *    shows the spinner. Download links (export/download/template) auto-recover in ~3.5s.
     * 3. Custom JS / Async Buttons (Class A): Call window.setButtonLoading(btn, true, 'Saving...')
     *    and restore with window.setButtonLoading(btn, false) in a finally block,
     *    OR wrap with window.withButtonLoading(btn, asyncFn, 'Saving...').
     * 4. Client-side Only (Class B): Modals, toggles, dismiss, cancel buttons receive
     *    instant press feedback via CSS without showing a spinner.
     * 5. Opt-Out (Class C): Add data-loader-skip attribute to completely bypass loaders.
     * ========================================================================= */

    if (!window.deriveLoadingText) {
        window.deriveLoadingText = function(btn) {
            if (!btn) return 'Loading...';
            var custom = btn.getAttribute('data-loading-text');
            if (custom) return custom;
            var raw = (btn.textContent || '').trim().replace(/\s+/g, ' ');
            if (!raw) return ''; // Icon-only button
            var lower = raw.toLowerCase();

            if (lower.startsWith('save')) return 'Saving...';
            if (lower.startsWith('add')) return 'Adding...';
            if (lower.startsWith('create')) return 'Creating...';
            if (lower.startsWith('update')) return 'Updating...';
            if (lower.startsWith('apply')) return 'Applying...';
            if (lower.startsWith('export')) return 'Exporting...';
            if (lower.startsWith('download')) return 'Preparing...';
            if (lower.startsWith('sign in') || lower.startsWith('login')) return 'Signing in...';
            if (lower.startsWith('sign up')) return 'Signing up...';
            if (lower.startsWith('restore') || lower.includes('restore this version')) return 'Restoring...';
            if (lower.startsWith('generate')) return 'Generating...';
            if (lower.startsWith('confirm')) return 'Confirming...';
            if (lower.startsWith('discard')) return 'Discarding...';
            if (lower.startsWith('filter')) return 'Filtering...';
            if (lower.startsWith('clear')) return 'Clearing...';
            if (lower.startsWith('reset')) return 'Resetting...';
            if (lower.startsWith('delete')) return 'Deleting...';
            if (lower.startsWith('remove')) return 'Removing...';
            if (lower.startsWith('search')) return 'Searching...';
            if (lower.startsWith('upload')) return 'Uploading...';
            if (lower.startsWith('import')) return 'Importing...';
            if (lower.startsWith('reject')) return 'Rejecting...';
            if (lower.startsWith('approve')) return 'Approving...';
            if (lower.startsWith('initialize')) return 'Initializing...';
            if (lower.startsWith('archive')) return 'Archiving...';
            if (lower.startsWith('view') || lower.startsWith('back')) return 'Loading...';

            var firstWord = raw.split(' ')[0].replace(/[^a-zA-Z]/g, '');
            if (firstWord.length > 2) {
                var wLower = firstWord.toLowerCase();
                if (wLower.endsWith('e') && !wLower.endsWith('ee')) {
                    return firstWord.slice(0, -1) + 'ing...';
                }
                return firstWord + 'ing...';
            }
            return 'Loading...';
        };
    }

    if (!window.setButtonLoading) {
        window.setButtonLoading = function(btn, isLoading, loadingText) {
            if (!btn) return;
            if (isLoading) {
                if (btn.__loaderState) return; // already loading

                var w = btn.offsetWidth;
                var h = btn.offsetHeight;
                var rawText = (btn.textContent || '').trim().replace(/\s+/g, ' ');
                var text = loadingText || btn.getAttribute('data-loading-text') || window.deriveLoadingText(btn);

                var state = {
                    html: btn.innerHTML,
                    width: w,
                    height: h,
                    disabled: btn.disabled,
                    disabledSiblings: []
                };

                if (w > 0) btn.style.minWidth = w + 'px';
                if (h > 0) btn.style.minHeight = h + 'px';

                if (!text && !rawText) {
                    btn.innerHTML = '<span class="btn-loading-spinner btn-loading-spinner--icon-only" aria-hidden="true"></span>';
                } else {
                    btn.innerHTML = '<span class="btn-loading-spinner" aria-hidden="true"></span>' +
                        '<span>' + (text || 'Loading...') + '</span>';
                }

                btn.disabled = true;
                btn.setAttribute('aria-busy', 'true');
                btn.setAttribute('aria-disabled', 'true');
                btn.classList.add('btn-loading');

                var container = btn.closest('form, .modal-content, .card-custom, .card, tr, .section-card, .delete-request-card, .list-group-item');
                if (container) {
                    var siblings = container.querySelectorAll('button:not(.btn-loading), a.btn:not(.btn-loading)');
                    siblings.forEach(function(sib) {
                        if (sib !== btn && !sib.classList.contains('btn-loading')) {
                            sib.__prevDisabled = sib.disabled;
                            sib.__prevPointerEvents = sib.style.pointerEvents;
                            sib.disabled = true;
                            sib.style.pointerEvents = 'none';
                            sib.classList.add('sibling-locked-mid-load');
                            state.disabledSiblings.push(sib);
                        }
                    });
                }

                btn.__loaderState = state;
            } else {
                if (!btn.__loaderState) return;

                if (btn.__loaderState.disabledSiblings) {
                    btn.__loaderState.disabledSiblings.forEach(function(sib) {
                        sib.disabled = !!sib.__prevDisabled;
                        sib.style.pointerEvents = sib.__prevPointerEvents || '';
                        sib.classList.remove('sibling-locked-mid-load');
                        delete sib.__prevDisabled;
                        delete sib.__prevPointerEvents;
                    });
                }

                btn.innerHTML = btn.__loaderState.html;
                btn.style.minWidth = '';
                btn.style.minHeight = '';
                btn.disabled = btn.__loaderState.disabled || false;
                btn.removeAttribute('aria-busy');
                btn.removeAttribute('aria-disabled');
                btn.classList.remove('btn-loading');
                btn.__loaderState = null;
            }
        };
    }

    if (!window.withButtonLoading) {
        window.withButtonLoading = function(btn, asyncFn, loadingText) {
            window.setButtonLoading(btn, true, loadingText);
            var result;
            try {
                result = asyncFn();
            } catch(e) {
                window.setButtonLoading(btn, false);
                throw e;
            }
            if (result && typeof result.then === 'function') {
                return result.then(
                    function(v) { window.setButtonLoading(btn, false); return v; },
                    function(e) { window.setButtonLoading(btn, false); throw e; }
                );
            }
            window.setButtonLoading(btn, false);
            return result;
        };
    }

    if (!window.__buttonDelegationBound) {
        window.__buttonDelegationBound = true;

        // 1. Form Submits (including Enter-key submissions)
        document.addEventListener('submit', function(e) {
            var form = e.target;
            if (!form || form.hasAttribute('data-loader-skip')) return;
            if (e.defaultPrevented) return;

            var submitBtn = e.submitter || form.querySelector('button[type="submit"], input[type="submit"]');
            if (!submitBtn || submitBtn.hasAttribute('data-loader-skip')) return;
            if (submitBtn.__loaderState || form.__isSubmitting) {
                e.preventDefault();
                return;
            }
            form.__isSubmitting = true;

            window.setButtonLoading(submitBtn, true);

            window.addEventListener('pageshow', function restore() {
                delete form.__isSubmitting;
                window.setButtonLoading(submitBtn, false);
                window.removeEventListener('pageshow', restore);
            }, { once: true });
        }, false);

        // 2. Link Clicks (Navigation & Downloads)
        document.addEventListener('click', function(e) {
            var link = e.target.closest('a.btn, a[class*="btn-"], a.sidebar__link, a.sidebar__link--logout');
            if (!link || link.hasAttribute('data-loader-skip')) return;

            if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
            if (link.hasAttribute('data-bs-toggle') || link.hasAttribute('data-bs-dismiss') || link.getAttribute('data-action') === 'delete') return;

            var href = link.getAttribute('href');
            if (!href || href === '#' || href.startsWith('javascript:')) return;

            var isDownload = href.indexOf('export') !== -1 || href.indexOf('download') !== -1 || href.indexOf('template') !== -1 || href.indexOf('backup') !== -1;
            if (isDownload) {
                var downloadText = link.getAttribute('data-loading-text') || (href.indexOf('export') !== -1 ? 'Exporting...' : 'Preparing...');
                window.setButtonLoading(link, true, downloadText);
                setTimeout(function() {
                    window.setButtonLoading(link, false);
                }, 3500);
                return;
            }

            if (window.AppLoader && (link.classList.contains('sidebar__link') || link.classList.contains('sidebar__link--logout'))) {
                window.AppLoader.startBar();
            }

            if (link.classList.contains('btn') || link.className.indexOf('btn-') !== -1) {
                window.setButtonLoading(link, true);
                var safetyTimer = setTimeout(function() {
                    window.setButtonLoading(link, false);
                }, 12000);
                window.addEventListener('pageshow', function restore() {
                    clearTimeout(safetyTimer);
                    window.setButtonLoading(link, false);
                    window.removeEventListener('pageshow', restore);
                }, { once: true });
            }
        }, false);

        // 3. Auto-restore on bfcache (back/forward navigation)
        window.addEventListener('pageshow', function() {
            if (window.AppLoader) {
                window.AppLoader.finishBar();
                window.AppLoader.hideGenOverlay();
            }
            document.querySelectorAll('.btn-loading').forEach(function(btn) {
                window.setButtonLoading(btn, false);
            });
            document.querySelectorAll('.sibling-locked-mid-load').forEach(function(el) {
                el.disabled = false;
                el.style.pointerEvents = '';
                el.classList.remove('sibling-locked-mid-load');
            });
        });
    }

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

    // ============================================================
    // UNIVERSAL DELETE SYSTEM & EVENT DELEGATION
    // ============================================================
    function updateCountBadges() {
        var badges = document.querySelectorAll('.card-custom__header .badge-custom, .card-custom__header .badge, .page-header .badge, .nav-pills .badge, [data-count-badge]');
        badges.forEach(function(badge) {
            var text = badge.textContent.trim();
            var match = text.match(/\b(\d+)\b/);
            if (match) {
                var currentNum = parseInt(match[1], 10);
                if (currentNum > 0) {
                    var newNum = currentNum - 1;
                    badge.textContent = text.replace(match[1], String(newNum));
                }
            }
        });
    }

    function executeUniversalDelete(deleteEl) {
        if (deleteEl.disabled || deleteEl.classList.contains('btn-loading') || deleteEl.__isDeleting) return;
        deleteEl.__isDeleting = true;

        var targetUrl = deleteEl.getAttribute('data-url') || deleteEl.getAttribute('href') || (deleteEl.form ? deleteEl.form.action : '');
        var method = deleteEl.getAttribute('data-method') || (deleteEl.tagName === 'A' ? 'GET' : 'POST');
        var loadingText = deleteEl.getAttribute('data-loading-text') || 'Deleting...';

        // 1. Find containing row/card
        var rowEl = deleteEl.closest('tr, .section-card, .delete-request-card, .list-group-item');

        // 2. Lock button state
        if (typeof window.setButtonLoading === 'function') {
            window.setButtonLoading(deleteEl, true, loadingText);
        } else {
            deleteEl.disabled = true;
        }

        // 3. Disable sibling buttons in the same row/card mid-delete
        var disabledSiblings = [];
        if (rowEl) {
            rowEl.classList.add('row-disabled-mid-delete');
            var siblings = rowEl.querySelectorAll('button:not(.btn-loading), a:not(.btn-loading)');
            siblings.forEach(function(sib) {
                if (sib !== deleteEl) {
                    sib.__prevDisabled = sib.disabled;
                    sib.__prevPointerEvents = sib.style.pointerEvents;
                    sib.disabled = true;
                    sib.style.pointerEvents = 'none';
                    disabledSiblings.push(sib);
                }
            });
        }

        function restoreSiblings() {
            delete deleteEl.__isDeleting;
            if (rowEl) {
                rowEl.classList.remove('row-disabled-mid-delete');
            }
            disabledSiblings.forEach(function(sib) {
                sib.disabled = !!sib.__prevDisabled;
                sib.style.pointerEvents = sib.__prevPointerEvents || '';
                delete sib.__prevDisabled;
                delete sib.__prevPointerEvents;
            });
        }

        // 4. Send request
        fetch(targetUrl, {
            method: method,
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json'
            }
        })
        .then(function(res) {
            var contentType = res.headers.get('content-type') || '';
            if (contentType.indexOf('application/json') !== -1) {
                return res.json().then(function(data) {
                    return { ok: res.ok, status: res.status, data: data };
                });
            } else {
                return res.text().then(function() {
                    return {
                        ok: res.ok,
                        status: res.status,
                        data: {
                            success: res.ok,
                            message: res.ok ? 'Deleted successfully.' : ('Delete failed with status ' + res.status)
                        }
                    };
                });
            }
        })
        .then(function(result) {
            var isSuccess = result.ok && (result.data.success !== false && result.data.ok !== false);
            if (!isSuccess) {
                var errorMsg = (result.data && (result.data.message || result.data.error)) || ('Failed to delete record (Status ' + result.status + ').');
                handleDeleteFailure(errorMsg);
                return;
            }

            // SUCCESS!
            handleDeleteSuccess(result.data);
        })
        .catch(function(err) {
            handleDeleteFailure(err.message || 'Network error occurred while deleting.');
        });

        function handleDeleteFailure(errorMsg) {
            // Restore button state
            if (typeof window.setButtonLoading === 'function') {
                window.setButtonLoading(deleteEl, false);
            } else {
                deleteEl.disabled = false;
            }
            restoreSiblings();

            // Shake row if simple and motion allowed
            if (rowEl) {
                var prefersReducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
                if (!prefersReducedMotion) {
                    rowEl.classList.add('row-delete-shake');
                    setTimeout(function() {
                        rowEl.classList.remove('row-delete-shake');
                    }, 400);
                }
            }

            if (typeof window.showToast === 'function') {
                window.showToast(errorMsg, 'error');
            } else {
                alert(errorMsg);
            }
        }

        function handleDeleteSuccess(data) {
            delete deleteEl.__isDeleting;
            var successMsg = (data && (data.message || data.success)) || 'Deleted successfully.';
            if (typeof window.showToast === 'function') {
                window.showToast(successMsg, 'success');
            }

            if (rowEl) {
                var prefersReducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
                var animDuration = prefersReducedMotion ? 0 : 280;

                rowEl.classList.add('row-deleting-fade');

                setTimeout(function() {
                    var parentTable = rowEl.closest('table');
                    var parentTbody = rowEl.closest('tbody');

                    rowEl.remove();

                    // Decrement count badges
                    updateCountBadges();

                    // Show empty state if table / container is now empty
                    if (parentTbody) {
                        var remainingRows = parentTbody.querySelectorAll('tr:not(.empty-state-row):not(.d-none)');
                        if (remainingRows.length === 0) {
                            var colCount = (parentTable && parentTable.querySelectorAll('thead th').length) || 6;
                            var emptyTr = document.createElement('tr');
                            emptyTr.className = 'empty-state-row';
                            emptyTr.innerHTML = '<td colspan="' + colCount + '" class="text-center py-5 text-muted">' +
                                '<div class="empty-state py-4"><i class="bx bx-folder-open fs-1 d-block mb-2 text-secondary opacity-50"></i>' +
                                '<div class="fw-medium">No records remaining</div></div></td>';
                            parentTbody.appendChild(emptyTr);
                        }
                    }
                }, animDuration);
            } else {
                setTimeout(function() {
                    window.location.reload();
                }, 300);
            }
        }
    }

    // Global Click Delegation
    document.addEventListener("click", function (e) {
        var deleteEl = e.target.closest("[data-action='delete'], [data-confirm]");
        if (!deleteEl) return;

        // Skip client-only file remove in import modal
        if (deleteEl.id === 'btn-remove-selected-file') return;

        e.preventDefault();
        e.stopPropagation();

        var message = deleteEl.getAttribute("data-confirm");
        var title = deleteEl.getAttribute("data-title") || deleteEl.getAttribute("title") || "";

        if (!title && message) {
            var msgLower = message.toLowerCase();
            if (msgLower.includes("course")) title = "Delete Course";
            else if (msgLower.includes("professor")) title = "Delete Professor";
            else if (msgLower.includes("room")) title = "Delete Room";
            else if (msgLower.includes("timeslot") || msgLower.includes("operating day")) title = "Delete Timeslot";
            else if (msgLower.includes("schedule")) title = "Delete Schedule";
            else if (msgLower.includes("user")) title = "Delete User";
            else if (msgLower.includes("program")) title = "Delete Program";
            else if (msgLower.includes("archive")) title = "Delete Archive Version";
            else title = "Delete Item";
        }

        var confirmText = deleteEl.getAttribute("data-confirm-text") || "Delete";
        var subtext = deleteEl.getAttribute("data-subtext") || "This action cannot be undone.";

        if (message && typeof window.showConfirmModal === "function") {
            window.showConfirmModal({
                title: title || "Confirm Delete",
                message: message,
                subtext: subtext,
                confirmText: confirmText,
                confirmBtnClass: "btn-danger",
                onConfirm: function () {
                    executeUniversalDelete(deleteEl);
                }
            });
        } else {
            executeUniversalDelete(deleteEl);
        }
    }, true);

    // bfcache restore handler for all delete buttons & rows
    window.addEventListener("pageshow", function () {
        document.querySelectorAll(".btn-loading").forEach(function (btn) {
            if (typeof window.setButtonLoading === "function") {
                window.setButtonLoading(btn, false);
            } else {
                btn.disabled = false;
            }
            delete btn.__isDeleting;
        });
        document.querySelectorAll(".row-disabled-mid-delete").forEach(function (row) {
            row.classList.remove("row-disabled-mid-delete");
        });
    });
})();
