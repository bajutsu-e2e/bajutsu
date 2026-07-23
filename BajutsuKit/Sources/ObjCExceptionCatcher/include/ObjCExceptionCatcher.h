#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

/// Runs a block inside an Objective-C `@try`/`@catch`, turning a raised `NSException` into a
/// returned error.
///
/// Swift's `do`/`catch` intercepts only Swift `Error`s, never an Objective-C `NSException`; an
/// uncaught `NSException` unwinds straight past every Swift frame and terminates the process. The
/// resident XCUITest runner hits exactly this: an element interaction (`XCUIElement.tap()` and the
/// like) raises an `NSException` when the element fails to resolve ("No matches found") — a normal
/// race when the screen shifts mid-tap — and, uncaught on the runner's serve loop, it aborts the
/// long-lived test method and the whole runner, leaving every later Python request with a bare
/// "connection refused". Bridging that exception back into Swift as a catchable failure lets the
/// runner report the miss and keep serving.
@interface ObjCExceptionCatcher : NSObject

/// Invoke `block`; return `YES` if it returned normally, or `NO` with `*error` describing the
/// caught `NSException` (its name and reason) if it raised one. The `error:` convention imports into
/// Swift as a throwing `catchException(_:)`.
+ (BOOL)catchException:(NS_NOESCAPE void (^)(void))block
                 error:(NSError *_Nullable *_Nullable)error;

@end

NS_ASSUME_NONNULL_END
