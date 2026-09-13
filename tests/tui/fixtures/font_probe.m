// font_probe — isolated CoreText font-resolution evidence.
//
// The product claim under test is not "a string appears in a plist". It is
// that the process which draws the terminal glyphs can resolve the bundled
// Spot Mono family, and that doing so costs no other process anything. Only a
// real consuming process can answer that, so this is a real consuming process.
//
// Usage:
//   font_probe <family>          resolve a family in THIS process, emit JSON
//   font_probe --families <path> name the families a font file declares
//
// Reads fonts. Registers nothing outside the caller. Writes no font store.
//
// 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
#import <CoreText/CoreText.h>
#import <Foundation/Foundation.h>

static NSString *DescriptorPath(CTFontDescriptorRef descriptor) {
  CFTypeRef value = CTFontDescriptorCopyAttribute(descriptor, kCTFontURLAttribute);
  if (value == NULL) {
    return nil;
  }
  NSString *path = [(__bridge NSURL *)value path];
  CFRelease(value);
  return path;
}

static NSString *DescriptorFamily(CTFontDescriptorRef descriptor) {
  CFTypeRef value = CTFontDescriptorCopyAttribute(descriptor, kCTFontFamilyNameAttribute);
  if (value == NULL) {
    return nil;
  }
  NSString *family = [NSString stringWithString:(__bridge NSString *)value];
  CFRelease(value);
  return family;
}

static int EmitFamilies(const char *path) {
  NSURL *url = [NSURL fileURLWithPath:[NSString stringWithUTF8String:path]];
  NSMutableArray<NSString *> *families = [NSMutableArray array];
  CFArrayRef descriptors = CTFontManagerCreateFontDescriptorsFromURL((__bridge CFURLRef)url);
  if (descriptors != NULL) {
    for (CFIndex i = 0; i < CFArrayGetCount(descriptors); i++) {
      NSString *family = DescriptorFamily(CFArrayGetValueAtIndex(descriptors, i));
      if (family != nil && ![families containsObject:family]) {
        [families addObject:family];
      }
    }
    CFRelease(descriptors);
  }
  NSData *json = [NSJSONSerialization dataWithJSONObject:@{@"families" : families}
                                                options:NSJSONWritingSortedKeys
                                                  error:NULL];
  fwrite(json.bytes, 1, json.length, stdout);
  fputc('\n', stdout);
  return 0;
}

int main(int argc, const char *argv[]) {
  @autoreleasepool {
    if (argc == 3 && strcmp(argv[1], "--families") == 0) {
      return EmitFamilies(argv[2]);
    }
    if (argc != 2) {
      fprintf(stderr, "usage: font_probe <family> | font_probe --families <path>\n");
      return 2;
    }
    NSString *family = [NSString stringWithUTF8String:argv[1]];
    NSBundle *main = [NSBundle mainBundle];

    CTFontDescriptorRef query = CTFontDescriptorCreateWithAttributes(
        (__bridge CFDictionaryRef) @{(id)kCTFontFamilyNameAttribute : family});
    NSSet *mandatory = [NSSet setWithObject:(id)kCTFontFamilyNameAttribute];

    // Every file this process can see for the family, not merely the winner:
    // presence proves registration, order proves precedence.
    NSMutableArray<NSString *> *matching = [NSMutableArray array];
    CFArrayRef all =
        CTFontDescriptorCreateMatchingFontDescriptors(query, (__bridge CFSetRef)mandatory);
    if (all != NULL) {
      for (CFIndex i = 0; i < CFArrayGetCount(all); i++) {
        NSString *path = DescriptorPath(CFArrayGetValueAtIndex(all, i));
        if (path != nil && ![matching containsObject:path]) {
          [matching addObject:path];
        }
      }
      CFRelease(all);
    }

    NSString *resolvedPath = nil;
    CTFontDescriptorRef best =
        CTFontDescriptorCreateMatchingFontDescriptor(query, (__bridge CFSetRef)mandatory);
    if (best != NULL) {
      resolvedPath = DescriptorPath(best);
      CFRelease(best);
    }
    CFRelease(query);

    // What a terminal really asks for. When the family is unavailable CoreText
    // answers with a substitute, so the family of the returned font — not the
    // fact that a font came back — is the honest success signal.
    NSString *usable = nil;
    CTFontRef font = CTFontCreateWithName((__bridge CFStringRef)family, 18.5, NULL);
    if (font != NULL) {
      CFStringRef name = CTFontCopyFamilyName(font);
      if (name != NULL) {
        usable = [NSString stringWithString:(__bridge NSString *)name];
        CFRelease(name);
      }
      CFRelease(font);
    }

    NSData *json = [NSJSONSerialization
        dataWithJSONObject:@{
          @"family" : family,
          @"main_bundle" : main.bundlePath ?: @"",
          @"declared_fonts_path" :
              [main objectForInfoDictionaryKey:@"ATSApplicationFontsPath"] ?: [NSNull null],
          @"matching_urls" : matching,
          @"resolved_url" : resolvedPath ?: [NSNull null],
          @"usable_family" : usable ?: [NSNull null],
        }
                   options:NSJSONWritingSortedKeys
                     error:NULL];
    fwrite(json.bytes, 1, json.length, stdout);
    fputc('\n', stdout);
    return 0;
  }
}
