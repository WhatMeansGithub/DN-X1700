/* DN-X1700 USB descriptor clone for a Black Pill (STM32F411CE, USB FS, libopencm3).
 *
 * Serves the descriptors macOS received from the real mixer (see desc_variants.h) so we can see which
 * descriptor changes make usbaudiod accept the device. No audio flows: endpoints are never opened.
 * KEY button (PA0) cycles the variant and re-enumerates; the LED (PC13) blinks the variant number + 1.
 */
#include <stdint.h>
#include <string.h>
#include <libopencm3/stm32/rcc.h>
#include <libopencm3/stm32/gpio.h>
#include <libopencm3/usb/dwc/otg_fs.h>
#include <libopencm3/usb/usbd.h>
#include "desc_variants.h"

static usbd_device *usbd;
static uint8_t control_buf[256] __attribute__((aligned(4)));
#define START_VARIANT 5                                 /* v5 = IAD class + AS controls only (the real patch set); KEY -> v6, v0, ... */
static volatile unsigned variant = START_VARIANT;

static const char *const strings[] = {"D&M--", "DN-X1700"};

static const struct usb_device_descriptor dev = {
    .bLength = 18, .bDescriptorType = USB_DT_DEVICE, .bcdUSB = 0x0200,
    .bDeviceClass = 0xEF, .bDeviceSubClass = 0x02, .bDeviceProtocol = 0x01, .bMaxPacketSize0 = 64,
    .idVendor = 0x154E, .idProduct = 0x500A, .bcdDevice = 0x0001,
    .iManufacturer = 1, .iProduct = 2, .iSerialNumber = 0, .bNumConfigurations = 1,
};

/* libopencm3 walks config->interface[] on SET_CONFIGURATION, so give it 6 empty interfaces.
 * The bytes the host actually reads come from variant_cfg[] (see the control callback). */
static const struct usb_interface_descriptor empty_alt = {
    .bLength = 9, .bDescriptorType = USB_DT_INTERFACE, .bNumEndpoints = 0,
};
static const struct usb_interface ifaces[6] = {
    {.num_altsetting = 1, .altsetting = &empty_alt}, {.num_altsetting = 1, .altsetting = &empty_alt},
    {.num_altsetting = 1, .altsetting = &empty_alt}, {.num_altsetting = 1, .altsetting = &empty_alt},
    {.num_altsetting = 1, .altsetting = &empty_alt}, {.num_altsetting = 1, .altsetting = &empty_alt},
};
static const struct usb_config_descriptor config = {
    .bLength = 9, .bDescriptorType = USB_DT_CONFIGURATION, .wTotalLength = 0, .bNumInterfaces = 6,
    .bConfigurationValue = 1, .iConfiguration = 0, .bmAttributes = 0xC0, .bMaxPower = 0, .interface = ifaces,
};

static void led(int on) { if (on) gpio_clear(GPIOC, GPIO13); else gpio_set(GPIOC, GPIO13); }

/* Mimic what the REAL X1700 answered in tools/probe_uac2.swift (captures/probe-uac2-real-x1700.txt):
 *   clock source 5 rate: GET_CUR ff ff ff ff, GET_RANGE one range 44100..44100
 *   clock selector 7 GET_CUR: 0xf9 (sic)
 *   everything else (clock validity, terminal/AS controls, latency, ...): STALL */
static uint32_t cur_rate = 0xFFFFFFFF;
static uint8_t sel_cur = 0xF9;                                                   /* real unit reads 0xf9 */
static uint8_t reply[64] __attribute__((aligned(4)));

static enum usbd_request_return_codes uac2_request(struct usb_setup_data *req, uint8_t **buf, uint16_t *len)
{
    uint8_t entity = req->wIndex >> 8, cs = req->wValue >> 8;
    int out = !(req->bmRequestType & 0x80);
    if ((entity == 5 || entity == 6) && cs == 1) {                               /* sampling frequency */
        if (out && req->bRequest == 1) {                                         /* SET_CUR: keep the value */
            if (*len >= 4) memcpy(&cur_rate, *buf, 4);
            return USBD_REQ_HANDLED;
        }
        if (req->bRequest == 1) { memcpy(reply, &cur_rate, 4); *len = 4; }
        else if (req->bRequest == 2) {
            const uint32_t r = 44100; const uint16_t n = 1;
            memcpy(reply, &n, 2); memcpy(reply + 2, &r, 4); memcpy(reply + 6, &r, 4); memset(reply + 10, 0, 4);
            *len = 14;
        } else return USBD_REQ_NOTSUPP;
    } else if (entity == 7 && cs == 1 && out && req->bRequest == 1) {            /* selector SET_CUR: the real unit ACCEPTS it ... */
        return USBD_REQ_HANDLED;                                                 /* ... and ignores it (probe: reads back 0xf9) */
    } else if (entity == 7 && cs == 1 && !out && req->bRequest == 1) { reply[0] = sel_cur; *len = 1; }
    else return USBD_REQ_NOTSUPP;
    if (*len > req->wLength) *len = req->wLength;
    *buf = reply;
    return USBD_REQ_HANDLED;
}

static enum usbd_request_return_codes control_cb(usbd_device *d, struct usb_setup_data *req, uint8_t **buf, uint16_t *len,
                      usbd_control_complete_callback *complete)
{
    (void)d; (void)complete;
    uint8_t type = req->bmRequestType & USB_REQ_TYPE_TYPE;
    gpio_toggle(GPIOC, GPIO13);
    if (type == USB_REQ_TYPE_STANDARD) {
        if (req->bRequest == USB_REQ_GET_DESCRIPTOR && (req->wValue >> 8) == USB_DT_CONFIGURATION) {
            const uint8_t *c = variant_cfg[variant];
            uint16_t n = 385;
            *buf = (uint8_t *)c;
            *len = req->wLength < n ? req->wLength : n;
            return USBD_REQ_HANDLED;
        }
        if (req->bRequest == USB_REQ_SET_INTERFACE) { *len = 0; return USBD_REQ_HANDLED; }
        if (req->bRequest == USB_REQ_GET_INTERFACE) { reply[0] = 0; *buf = reply; *len = 1; return USBD_REQ_HANDLED; }
        return USBD_REQ_NEXT_CALLBACK;                                           /* library handles the rest */
    }
    if (type == USB_REQ_TYPE_CLASS && (req->bmRequestType & USB_REQ_TYPE_RECIPIENT) == USB_REQ_TYPE_INTERFACE
        && (req->wIndex & 0xFF) == 0)
        return uac2_request(req, buf, len);
    return USBD_REQ_NOTSUPP;
}

static void blink(unsigned n)
{
    for (unsigned i = 0; i < n; i++) {
        led(1); for (volatile int k = 0; k < 2000000; k++);
        led(0); for (volatile int k = 0; k < 2000000; k++);
    }
}

int main(void)
{
    rcc_clock_setup_pll(&rcc_hse_25mhz_3v3[RCC_CLOCK_3V3_84MHZ]);                /* Black Pill: 25 MHz HSE, 48 MHz USB clock */
    rcc_periph_clock_enable(RCC_GPIOA);
    rcc_periph_clock_enable(RCC_GPIOC);
    rcc_periph_clock_enable(RCC_OTGFS);
    gpio_mode_setup(GPIOC, GPIO_MODE_OUTPUT, GPIO_PUPD_NONE, GPIO13);
    gpio_mode_setup(GPIOA, GPIO_MODE_INPUT, GPIO_PUPD_PULLUP, GPIO0);            /* KEY */
    gpio_mode_setup(GPIOA, GPIO_MODE_AF, GPIO_PUPD_NONE, GPIO11 | GPIO12);
    gpio_set_af(GPIOA, GPIO_AF10, GPIO11 | GPIO12);

    usbd = usbd_init(&otgfs_usb_driver, &dev, &config, strings, 2, control_buf, sizeof(control_buf));
    /* libopencm3 enables VBUS sensing, but the Black Pill doesn't wire VBUS to PA9: the core would think no host is
     * attached and never raise D+. Force "no VBUS sensing" (bit 21 = NOVBUSSENS on this core, VBDEN on newer ones). */
    if (OTG_FS_CID < OTG_CID_HAS_VBDEN) {
        OTG_FS_GCCFG &= ~(OTG_GCCFG_VBUSBSEN | OTG_GCCFG_VBUSASEN);
        OTG_FS_GCCFG |= OTG_GCCFG_NOVBUSSENS;
    }
    usbd_register_control_callback(usbd, 0, 0, control_cb);                      /* catch-all, runs before the library */

    blink(variant + 1);
    for (;;) {
        usbd_poll(usbd);
        if (!gpio_get(GPIOA, GPIO0)) {                                           /* KEY pressed: next variant, re-enumerate */
            for (volatile int k = 0; k < 400000; k++) usbd_poll(usbd);           /* crude debounce */
            while (!gpio_get(GPIOA, GPIO0)) usbd_poll(usbd);
            usbd_disconnect(usbd, true);
            variant = (variant + 1) % NUM_VARIANTS;
            blink(variant + 1);
            usbd_disconnect(usbd, false);
        }
    }
}
