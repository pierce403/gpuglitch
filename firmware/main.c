#include "pico/stdlib.h"
#include "pico/stdio_usb.h"

int main(void) {
    // The host reads the board target and version from the USB product descriptor.
    stdio_usb_init();
    while (true) {
        sleep_ms(1000);
    }
}
