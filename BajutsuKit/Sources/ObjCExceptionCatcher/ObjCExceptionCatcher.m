#import "ObjCExceptionCatcher.h"

@implementation ObjCExceptionCatcher

+ (BOOL)catchException:(NS_NOESCAPE void (^)(void))block
                 error:(NSError *_Nullable *_Nullable)error {
    @try {
        block();
        return YES;
    } @catch (NSException *exception) {
        if (error) {
            NSMutableDictionary *info = [NSMutableDictionary dictionary];
            if (exception.name) {
                info[@"NSExceptionName"] = exception.name;
            }
            // Surface the exception's reason as the error's description so a caller that logs the
            // failure keeps the XCUITest diagnostic ("No matches found …") rather than an opaque code.
            info[NSLocalizedDescriptionKey] = exception.reason ?: exception.name ?: @"NSException";
            *error = [NSError errorWithDomain:@"ObjCExceptionCatcher" code:0 userInfo:info];
        }
        return NO;
    }
}

@end
