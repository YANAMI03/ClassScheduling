// Simulate event propagation and listener execution

class Event {
    constructor(type) {
        this.type = type;
        this.defaultPrevented = false;
        this.target = null;
        this.submitter = null;
    }
    preventDefault() {
        this.defaultPrevented = true;
    }
}

// Mock elements
const submitBtn = {
    tagName: 'BUTTON',
    type: 'submit',
    disabled: false,
    classList: {
        contains: (cls) => false,
        add: () => {},
        remove: () => {}
    },
    hasAttribute: (attr) => false,
    getAttribute: (attr) => null,
    setAttribute: () => {},
    removeAttribute: () => {},
    closest: () => null,
    offsetWidth: 100,
    offsetHeight: 40,
    style: {}
};

const form = {
    tagName: 'FORM',
    hasAttribute: (attr) => false,
    querySelector: (sel) => submitBtn
};

const windowMock = {
    setButtonLoading: function(btn, isLoading, loadingText) {
        if (isLoading) {
            if (btn.__loaderState) return;
            btn.__loaderState = { disabled: btn.disabled };
            btn.disabled = true;
        } else {
            if (!btn.__loaderState) return;
            btn.disabled = btn.__loaderState.disabled;
            btn.__loaderState = null;
        }
    },
    AppLoader: {
        showGenOverlay: (opts) => console.log("AppLoader.showGenOverlay shown:", opts.title)
    }
};

// Listeners list representing:
// 1. form listener in index.html
// 2. document listener in static/index.js
// 3. document listener in templates/base.html

const formListeners = [];
const docListeners = [];

// Listener 1: index.html form submit handler
formListeners.push(function indexHtmlFormHandler(e) {
    console.log("1. index.html form submit handler running...");
    const semesterSelect = { value: '2nd Semester' };
    var s = semesterSelect.value.trim();
    if (!s) {
        e.preventDefault();
        return;
    }
    if (submitBtn && submitBtn.disabled) {
        console.log("   index.html PREVENTED DEFAULT because submitBtn.disabled is true!");
        e.preventDefault();
        return;
    }
    windowMock.AppLoader.showGenOverlay({ title: 'Generating schedule...' });
});

// Listener 2: static/index.js document submit handler
docListeners.push(function indexJsDocHandler(e) {
    console.log("2. static/index.js document submit handler running...");
    var formTarget = e.target;
    if (!formTarget || formTarget.hasAttribute('data-loader-skip')) return;
    if (e.defaultPrevented) {
        console.log("   static/index.js: e.defaultPrevented already true, skipping");
        return;
    }

    var btn = e.submitter || formTarget.querySelector('button[type="submit"]');
    if (btn.__loaderState || formTarget.__isSubmitting) {
        console.log("   static/index.js: double submission blocked! Calling preventDefault()");
        e.preventDefault();
        return;
    }
    formTarget.__isSubmitting = true;
    windowMock.setButtonLoading(btn, true);
    console.log("   static/index.js: set formTarget.__isSubmitting = true and setButtonLoading(btn, true)");
});

// Listener 3: templates/base.html document submit handler
docListeners.push(function baseHtmlDocHandler(e) {
    console.log("3. templates/base.html document submit handler running...");
    var formTarget = e.target;
    if (!formTarget || formTarget.hasAttribute('data-loader-skip')) return;
    if (e.defaultPrevented) {
        console.log("   base.html: e.defaultPrevented already true, skipping");
        return;
    }

    var btn = e.submitter || formTarget.querySelector('button[type="submit"]');
    if (btn.__loaderState || formTarget.__isSubmitting) {
        console.log("   base.html: DOUBLE SUBMISSION BLOCKED! CALLING preventDefault()");
        e.preventDefault();
        return;
    }
    formTarget.__isSubmitting = true;
    windowMock.setButtonLoading(btn, true);
});

// Now simulate form submit event
const event = new Event('submit');
event.target = form;
event.submitter = submitBtn;

console.log("=== SIMULATING FORM SUBMISSION ===");
for (let h of formListeners) {
    h(event);
}
for (let h of docListeners) {
    h(event);
}

console.log("\n=== FINAL RESULT ===");
console.log("event.defaultPrevented:", event.defaultPrevented);
if (event.defaultPrevented) {
    console.log("FAILURE: Form submission was CANCELLED by preventDefault()! No HTTP request is sent!");
} else {
    console.log("SUCCESS: Form would submit normally to Flask!");
}
