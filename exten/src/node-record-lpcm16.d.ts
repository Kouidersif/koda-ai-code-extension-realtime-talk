declare module 'node-record-lpcm16' {
    import { Readable } from 'stream';

    export interface RecordOptions {
        sampleRate?: number;
        channels?: number;
        threshold?: number;
        recorder?: 'sox' | 'arecord' | 'rec';
        endOnSilence?: boolean;
        silence?: string;
        device?: string;
        audioType?: string;
        silence_threshold?: number;
        silence_duration?: number;
    }

    export interface Recording {
        stream(): Readable;
        stop(): void;
        pause(): void;
        resume(): void;
    }

    export function record(options?: RecordOptions): Recording;
}