# Automation Testing Guidelines

## Overview

The CSUI codebase uses a dual-layer testing strategy: **Karma/Jasmine** for unit and integration tests, and **CodeceptJS** with Playwright for end-to-end (E2E) automation tests. Karma tests run quickly in isolation, while CodeceptJS scenarios test real user workflows against a running server. Both are essential—unit tests catch regressions early, while E2E tests validate the complete feature flow. Choose the right tool: fast feedback loops with Karma, user-focused validation with CodeceptJS.

## Scenario Structure

Each CodeceptJS scenario must follow a clear **Arrange-Act-Assert** pattern. Start with setup (navigate, login if needed), perform user actions (clicks, form entries, selections), and **always end with explicit assertions** that verify the expected outcome. A scenario without assertions is incomplete—it may run without errors but won't validate anything. Assertions should match what users see: visible elements, state changes, notifications, or URL changes. Keep each scenario focused on one user journey; if you're testing multiple features, split them into separate scenarios.

**Pattern:**
Scenario('User action produces expected result', async (I) => {
I.amOnPage('/path');
I.fillField('input', 'value');
I.click('button');
I.seeTextEquals('Expected text', '.result');
});

## Helper Method Best Practices

Create helper methods for **reusable, single-responsibility actions**. A good helper does one thing well: login, fill a specific form, navigate to a feature, or verify a component state. Name helpers with **verb-based prefixes** (`login`, `fillUserForm`, `navigateToWidgets`, `verifySuccessMessage`). This makes test code read like human actions. Keep parameters minimal and meaningful. Avoid building massive "do-everything" helpers—they become brittle and hard to maintain. When a helper becomes complex (many conditionals, nested calls), it's a sign you should split it into smaller helpers.

## Selectors & Locators

Brittle selectors break when CSS changes. Avoid relying on tag sequences, index positions, or class names that developers might refactor. Instead, prefer **semantic HTML attributes**: data-attributes (`data-testid`, `data-element-id`), stable IDs, or ARIA labels. These won't change when styling updates happen. When selectors must use classes, pick ones tied to functionality, not appearance. Use CSS selectors for simple cases; switch to XPath only when necessary (nested conditions, complex traversal). Example: use `[data-element-id="submit-button"]` instead of `button:nth-child(3)`. Review selectors during code review—if they look fragile, they probably are.

## Assertions & Verification

Assertions are your test's contract with reality. Use specific assertions (`seeTextEquals`, `seeInCurrentUrl`) rather than generic ones—vague checks hide failures. Avoid assertion chains that mask individual failures; each assertion should be independent. Wait for elements before asserting—use `waitForElement` or conditional assertions that retry. Flaky tests often stem from **timing issues**: JavaScript hasn't finished rendering, network requests are in-flight, or animations are incomplete. Prefer data-driven verification over visual checks. When tests fail, error messages should be clear: `I.see('Success message', '.toast')` is better than `I.see('Success')`.

## Test Data Management

Keep test data simple. Use **existing data already in the system** whenever possible (test users, predefined entities). Avoid creating complex fixtures or mock objects in tests—if you need custom data, create it through the UI or API helper methods, not hardcoded literals. Reuse login credentials and standard objects across scenarios. Document any special test data (admin account, test tenant) in comments. If tests need fresh data per run, clean up in a hook `_after()`, not in individual scenarios.

## Common Mistakes to Avoid

- **Don't put setup/teardown in scenarios**—use `_before()` and `_after()` hooks instead.
- **Don't hardcode waits** like `I.wait(2000)`—wait for elements or conditions instead.
- **Don't test multiple features in one scenario**—split them; complex scenarios fail for unclear reasons.
- **Don't ignore accessibility**—selectors like `data-testid` and ARIA labels make tests robust and keep developers honest.
- **Don't verify internal state directly**—test what users see, not what's in JavaScript objects.
- **Don't repeat selectors everywhere**—define them in helpers or constants so changes are centralized.
- **Don't commit test code with `skip()` or `.only()`**—remove them before pushing.

## Conditional Logic Anti-Pattern ⚠️

**Never use conditional logic to skip assertions or change expected behavior.**

Conditional selectors that choose between alternatives hide failures and mask broken functionality. This pattern is dangerous:

```javascript
// ❌ WRONG - This always passes, even if both elements are missing
const footerCount = await I.grabNumberOfVisibleElements('.tile-footer-legacy');
if(footerCount > 0) {
  I.click('.tile-footer-legacy .action-button');
} else {
  I.click('.tile-footer-new .action-button');
}
```

**Why this fails:**
- If neither footer element exists, the test still passes (just executes the else branch)
- If the DOM structure changes unexpectedly, you won't detect it—the test silently adapts
- It masks regressions where the old footer should be replaced but isn't
- It tests the conditional logic, not the actual feature behavior
- Developers might remove one footer class, but the test keeps passing because it falls back to the other

**Correct approach:** Explicitly test for the expected element. If your code must support multiple variants, create separate test scenarios for each:

```javascript
// ✅ CORRECT - Explicitly verify which footer version is present
Scenario('Verify new tile footer is rendered with action button', async (I) => {
  I.scrollTo('.tile-footer-new');
  I.waitForVisible('.tile-footer-new');
  I.seeElement('.tile-footer-new .action-button');
  I.click('.tile-footer-new .action-button');
});

Scenario('Verify legacy tile footer is rendered with action button', async (I) => {
  I.scrollTo('.tile-footer-legacy');
  I.waitForVisible('.tile-footer-legacy');
  I.seeElement('.tile-footer-legacy .action-button');
  I.click('.tile-footer-legacy .action-button');
});
```

Or, if you must handle both in one test, document the choice and verify it explicitly:

```javascript
// ✅ BETTER - Fail clearly if expected footer is missing
let footerSelector;
try {
  I.waitForVisible('.tile-footer-new', 2);
  footerSelector = '.tile-footer-new .action-button';
} catch (e) {
  I.waitForVisible('.tile-footer-legacy .action-button', 2); // Fails if neither exists
  footerSelector = '.tile-footer-legacy .action-button';
}
I.click(footerSelector);
I.seeElement('.action-result'); // Verify the action succeeded
```

**Real-world example from CSUI:**
The problematic code in `list.view.helper.test.js` (lines 99-104) uses a count check to choose between header types:

```javascript
// ❌ WRONG - Hides missing header element
const headerCount = await I.grabNumberOfVisibleElements(widgetInTile + ' .smart-tile-header');
if(headerCount > 0) {
  I.moveCursorTo(widgetInTile + ' .smart-tile-header');
} else {
  I.moveCursorTo(widgetInTile + ' .tile-header');
}
```

Should be replaced with:

```javascript
// ✅ CORRECT - Fails if neither header exists
I.waitForVisible(widgetInTile + ' .smart-tile-header');
I.moveCursorTo(widgetInTile + ' .smart-tile-header');
```

This way, if the expected header is missing or both are present unexpectedly, the test **fails loudly** and alerts you to the regression.

## CodeceptJS Playwright Helper Methods Reference

The CodeceptJS Playwright helper provides a comprehensive set of methods for interacting with web applications. Understanding available methods helps you use the right tool for each task, avoiding workarounds that mask failures.

### Key Helper Methods to Use

#### **Navigation & Page State**
- `I.amOnPage(url)` - Navigate to a URL
- `I.seeInCurrentUrl(fragment)` - Verify URL contains text
- `I.seeCurrentUrlEquals(url)` - Assert exact URL match
- `I.refreshPage()` - Reload current page

#### **Element Visibility & Existence**
- `I.seeElement(locator)` - Element is visible in DOM
- `I.dontSeeElement(locator)` - Element is not visible
- `I.seeElementInDOM(locator)` - Element exists in DOM (not necessarily visible)
- `I.dontSeeElementInDOM(locator)` - Element does not exist in DOM
- `I.scrollTo(locator)` - Scroll element into view

#### **Waiting Methods** (Always prefer over hardcoded `I.wait()`)
- `I.waitForVisible(locator, timeout)` - Wait for element to become visible
- `I.waitForElement(locator, timeout)` - Wait for element to be in DOM
- `I.waitForInvisible(locator, timeout)` - Wait for element to become invisible
- `I.waitForDetached(locator, timeout)` - Wait for element to be removed from DOM
- `I.waitForClickable(locator, timeout)` - Wait for element to be clickable
- `I.waitForText(text, timeout, context)` - Wait for text to appear
- `I.waitForValue(field, value, timeout)` - Wait for input field value

#### **Text Verification**
- `I.see(text, context)` - Text is visible (optional context)
- `I.dontSee(text, context)` - Text is not visible
- `I.seeTextEquals(text, locator)` - Exact text match
- `I.seeInTitle(text)` - Title contains text
- `I.seeTitleEquals(text)` - Exact title match
- `I.seeInSource(text)` - Text in page source code

#### **Form Interactions**
- `I.fillField(locator, value)` - Clear and fill input field
- `I.appendField(locator, value)` - Append text to field
- `I.clearField(locator)` - Clear input/textarea
- `I.selectOption(locator, option)` - Select from dropdown
- `I.checkOption(locator)` - Check checkbox
- `I.uncheckOption(locator)` - Uncheck checkbox

#### **Grabbing/Retrieving Data** (Use with `await`)
- `await I.grabTextFrom(locator)` - Get element text
- `await I.grabAttributeFrom(locator, attr)` - Get element attribute
- `await I.grabValueFrom(locator)` - Get input field value
- `await I.grabCurrentUrl()` - Get current page URL
- `await I.grabTitle()` - Get page title
- `await I.grabNumberOfVisibleElements(locator)` - Count visible elements

#### **Clicks & Interactions**
- `I.click(locator, context, options)` - Click element
- `I.doubleClick(locator, context)` - Double-click
- `I.rightClick(locator, context)` - Right-click
- `I.moveCursorTo(locator, offsetX, offsetY)` - Move cursor to element
- `I.forceClick(locator)` - JavaScript click (for hidden elements)

#### **Keyboard Input**
- `I.pressKey(key)` - Press single key (e.g., 'Enter', 'Tab', 'Escape')
- `I.pressKey(['Control', 'Z'])` - Key combination
- `I.type(text, delay)` - Type text character by character

#### **Network & Traffic Mocking**
- `I.startRecordingTraffic()` - Record network requests
- `I.stopRecordingTraffic()` - Stop recording
- `await I.grabRecordedNetworkTraffics()` - Get all recorded traffic
- `I.blockTraffic(urls)` - Block specific URLs
- `I.mockTraffic(urls, response)` - Mock network responses
- `I.mockRoute(url, handler)` - Intercept and handle requests

#### **File & Download Handling**
- `I.attachFile(locator, filePath)` - Upload file
- `I.handleDownloads(fileName)` - Handle file downloads

#### **Browser State**
- `I.setCookie(cookie)` - Set cookie
- `I.seeCookie(name)` - Verify cookie exists
- `I.clearCookie(name)` - Clear cookie
- `await I.grabCookie(name)` - Get cookie value

#### **Assertions on Form Fields**
- `I.seeCheckboxIsChecked(locator)` - Checkbox is checked
- `I.dontSeeCheckboxIsChecked(locator)` - Checkbox is not checked
- `I.seeInField(locator, value)` - Field contains value
- `I.dontSeeInField(locator, value)` - Field does not contain value

#### **Advanced: Execute JavaScript**
- `I.executeScript(fn, arg)` - Execute JS in browser context
- `await I.executeScript(({x, y}) => x + y, {x, y})` - With parameters
- `I.usePlaywrightTo('description', async ({page, browser, browserContext}) => {...})` - Direct Playwright API access

### Anti-Patterns: What NOT to Do

❌ **Don't use `I.wait()` for waits**
```javascript
// WRONG
I.fillField('search', 'query');
I.wait(2); // Arbitrary sleep
I.see('Results');
```

✅ **Do use element-based waits**
```javascript
// CORRECT
I.fillField('search', 'query');
I.waitForVisible('.results');
I.see('Results');
```

---

❌ **Don't use `grabNumberOfVisibleElements()` for conditional logic**
```javascript
// WRONG - hides failures
const count = await I.grabNumberOfVisibleElements('.item');
if(count > 0) {
  I.click('.item');
}
```

✅ **Do use `waitForVisible()` with direct assertion**
```javascript
// CORRECT - fails if item missing
I.waitForVisible('.item');
I.click('.item');
```

---

❌ **Don't grab data when you should verify UI state**
```javascript
// WRONG - testing implementation, not user experience
const items = await I.grabTextFromAll('.list-item');
if(items.includes('Expected')) { ... }
```

✅ **Do use appropriate see/verification methods**
```javascript
// CORRECT - testing what user sees
I.see('Expected', '.list-item');
```

---

❌ **Don't use generic assertions**
```javascript
// WEAK - unclear what we're checking
I.see('Error');
```

✅ **Do use specific assertions with context**
```javascript
// CLEAR - explicit intent and location
I.see('Error message', '.error-toast');
I.seeInField('username', 'john@example.com');
```

### Best Practices

1. **Always wait before asserting**: Use appropriate wait methods before checking element state
2. **Use semantic locators**: Prefer `data-testid`, `aria-label`, IDs over fragile classes
3. **One assertion per concern**: Each `I.see()` or wait should verify one thing
4. **Grab data only when necessary**: Most of the time, verify visibility/content instead
5. **Document mocking intent**: When using network mocking, comment why you're mocking
6. **Use context parameters**: Narrow down searches with context to avoid false positives

For complete documentation and all available methods, visit: https://codecept.io/helpers/Playwright/


