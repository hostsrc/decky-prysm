/*
 * pw_capture — Minimal PipeWire capture via portal FD.
 *
 * Takes a PipeWire FD (from portal OpenPipeWireRemote) and node ID,
 * connects to the PipeWire remote, captures video frames, and writes
 * raw NV12/BGRx to stdout for piping to FFmpeg.
 *
 * Usage: pw_capture <pw_fd> <node_id> [width] [height]
 *
 * This must run in the same process that obtained the portal FD,
 * or the FD must be passed via Unix socket / fork.
 */

#include <pipewire/pipewire.h>
#include <spa/param/video/format-utils.h>
#include <spa/debug/types.h>
#include <spa/param/video/type-info.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>

struct capture_data {
    struct pw_main_loop *loop;
    struct pw_core *core;
    struct pw_stream *stream;

    struct spa_video_info format;
    int width;
    int height;
    int stride;
    uint64_t frame_count;
    int got_format;
};

static int running = 1;

static void sigint_handler(int sig) {
    running = 0;
}

static void on_process(void *userdata)
{
    struct capture_data *data = userdata;
    struct pw_buffer *buf;
    struct spa_buffer *sbuf;
    void *ptr;

    if ((buf = pw_stream_dequeue_buffer(data->stream)) == NULL) {
        return;
    }

    sbuf = buf->buffer;
    if ((ptr = sbuf->datas[0].data) == NULL) {
        pw_stream_queue_buffer(data->stream, buf);
        return;
    }

    /* Write raw frame to stdout */
    size_t size = sbuf->datas[0].chunk->size;
    if (size > 0) {
        ssize_t written = write(STDOUT_FILENO, ptr, size);
        if (written < 0) {
            /* Broken pipe — FFmpeg closed */
            running = 0;
        }
        data->frame_count++;
        if (data->frame_count <= 3 || data->frame_count % 300 == 0) {
            fprintf(stderr, "[pw_capture] frame #%lu: %zu bytes (%dx%d)\n",
                    data->frame_count, size, data->width, data->height);
        }
    }

    pw_stream_queue_buffer(data->stream, buf);
}

static void on_param_changed(void *userdata, uint32_t id, const struct spa_pod *param)
{
    struct capture_data *data = userdata;

    if (param == NULL || id != SPA_PARAM_Format)
        return;

    if (spa_format_video_raw_parse(param, &data->format.info.raw) < 0)
        return;

    data->width = data->format.info.raw.size.width;
    data->height = data->format.info.raw.size.height;
    data->got_format = 1;

    fprintf(stderr, "[pw_capture] Format: %dx%d @ %d/%d fps, format=%d\n",
            data->width, data->height,
            data->format.info.raw.framerate.num,
            data->format.info.raw.framerate.denom,
            data->format.info.raw.format);
}

static void on_state_changed(void *userdata, enum pw_stream_state old,
                             enum pw_stream_state state, const char *error)
{
    fprintf(stderr, "[pw_capture] Stream state: %s → %s%s%s\n",
            pw_stream_state_as_string(old),
            pw_stream_state_as_string(state),
            error ? " error=" : "",
            error ? error : "");

    if (state == PW_STREAM_STATE_ERROR) {
        running = 0;
    }
}

static const struct pw_stream_events stream_events = {
    PW_VERSION_STREAM_EVENTS,
    .state_changed = on_state_changed,
    .process = on_process,
    .param_changed = on_param_changed,
};

int main(int argc, char *argv[])
{
    if (argc < 3) {
        fprintf(stderr, "Usage: %s <pw_fd> <node_id> [width] [height]\n", argv[0]);
        return 1;
    }

    int pw_fd = atoi(argv[1]);
    int node_id = atoi(argv[2]);
    int req_width = argc > 3 ? atoi(argv[3]) : 0;
    int req_height = argc > 4 ? atoi(argv[4]) : 0;

    signal(SIGINT, sigint_handler);
    signal(SIGPIPE, SIG_IGN);

    pw_init(NULL, NULL);

    struct capture_data data = {0};
    data.loop = pw_main_loop_new(NULL);
    struct pw_context *context = pw_context_new(
        pw_main_loop_get_loop(data.loop), NULL, 0);

    /* Connect to PipeWire via the portal FD */
    fprintf(stderr, "[pw_capture] Connecting via FD %d to node %d\n", pw_fd, node_id);
    data.core = pw_context_connect_fd(context, pw_fd, NULL, 0);
    if (data.core == NULL) {
        fprintf(stderr, "[pw_capture] ERROR: Failed to connect via FD %d\n", pw_fd);
        return 1;
    }
    fprintf(stderr, "[pw_capture] Connected to PipeWire!\n");

    /* Create stream */
    struct pw_properties *props = pw_properties_new(
        PW_KEY_MEDIA_TYPE, "Video",
        PW_KEY_MEDIA_CATEGORY, "Capture",
        PW_KEY_MEDIA_ROLE, "Screen",
        NULL);

    data.stream = pw_stream_new(data.core, "prysm-capture", props);
    struct spa_hook stream_listener;
    pw_stream_add_listener(data.stream, &stream_listener, &stream_events, &data);

    /* Build format params */
    uint8_t buffer[1024];
    struct spa_pod_builder b = SPA_POD_BUILDER_INIT(buffer, sizeof(buffer));

    struct spa_rectangle size = SPA_RECTANGLE(
        req_width > 0 ? req_width : 1280,
        req_height > 0 ? req_height : 800);
    struct spa_fraction framerate = SPA_FRACTION(0, 1);  /* negotiate any */

    const struct spa_pod *params[1];
    params[0] = spa_pod_builder_add_object(&b,
        SPA_TYPE_OBJECT_Format, SPA_PARAM_EnumFormat,
        SPA_FORMAT_mediaType,    SPA_POD_Id(SPA_MEDIA_TYPE_video),
        SPA_FORMAT_mediaSubtype, SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw),
        SPA_FORMAT_VIDEO_format, SPA_POD_CHOICE_ENUM_Id(3,
            SPA_VIDEO_FORMAT_BGRx,
            SPA_VIDEO_FORMAT_BGRx,
            SPA_VIDEO_FORMAT_NV12),
        SPA_FORMAT_VIDEO_size,   SPA_POD_Rectangle(&size),
        SPA_FORMAT_VIDEO_framerate, SPA_POD_Fraction(&framerate));

    /* Connect to the target node */
    pw_stream_connect(data.stream,
        PW_DIRECTION_INPUT,
        node_id,
        PW_STREAM_FLAG_AUTOCONNECT |
        PW_STREAM_FLAG_MAP_BUFFERS,
        params, 1);

    fprintf(stderr, "[pw_capture] Waiting for frames...\n");

    /* Run until interrupted or pipe breaks */
    while (running) {
        pw_main_loop_run(data.loop);
    }

    pw_stream_destroy(data.stream);
    pw_core_disconnect(data.core);
    pw_context_destroy(context);
    pw_main_loop_destroy(data.loop);
    pw_deinit();

    fprintf(stderr, "[pw_capture] Done. %lu frames captured.\n", data.frame_count);
    return 0;
}
