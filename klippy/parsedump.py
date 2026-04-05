#!/usr/bin/env python
# Script to parse a serial port data dump
#
# Copyright (C) 2016-2021  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os
import sys
import logging
import argparse
import time
import json
import re
import multiprocessing
from typing import List, Dict, Any, Optional, Iterator
import msgproto

class DumpParser:
    """
    High-performance parser for MCU serial data dumps.
    
    Attributes:
        mp (msgproto.MessageParser): The protocol parser instance.
        filter_re (re.Pattern): Compiled regex for filtering messages.
    """
    def __init__(self, dictionary: bytes, filter_pattern: Optional[str] = None):
        self.mp = msgproto.MessageParser()
        self.mp.process_identify(dictionary, decompress=False)
        self.filter_re = re.compile(filter_pattern) if filter_pattern else None

    def parse_packet(self, raw_packet: bytes) -> List[str]:
        """
        Parses a single raw packet and returns formatted messages.
        
        Args:
            raw_packet (bytes): The raw packet data.
            
        Returns:
            List[str]: Formatted message strings.
        """
        msgs = self.mp.dump(raw_packet)
        # Skip the sequence number (first element) and return decoded messages
        return [msg for msg in msgs[1:] if not self.filter_re or self.filter_re.search(msg)]

    def stream_packets(self, data_file: str) -> Iterator[bytes]:
        """
        Generates packets from a data file using memory-efficient streaming.
        
        Args:
            data_file (str): Path to the serial data dump file.
            
        Yields:
            bytes: Individual raw packets.
        """
        buffer = bytearray()
        with open(data_file, 'rb') as f:
            while True:
                chunk = f.read(256 * 1024)  # 256KB buffer for balanced I/O
                if not chunk:
                    break
                buffer.extend(chunk)
                
                while buffer:
                    l = self.mp.check_packet(buffer)
                    if l == 0:
                        break
                    if l < 0:
                        # Skip invalid data until next sync byte
                        del buffer[:-l]
                        continue
                    
                    yield bytes(buffer[:l])
                    del buffer[:l]

def process_chunk(args):
    """Worker function for parallel processing."""
    dictionary, packets, filter_pattern = args
    parser = DumpParser(dictionary, filter_pattern)
    results = []
    for pkt in packets:
        results.extend(parser.parse_packet(pkt))
    return results

def main():
    parser = argparse.ArgumentParser(description="Parse a serial port data dump")
    parser.add_argument("dict", help="MCU dictionary file")
    parser.add_argument("data", help="Serial data dump file")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("-o", "--output", help="Output file (default is stdout)")
    parser.add_argument("-f", "--filter", help="Filter messages by name (regex)")
    parser.add_argument("-s", "--stats", action="store_true", help="Show traffic statistics")
    parser.add_argument("--json", action="store_true", help="Output in JSON format")
    parser.add_argument("--parallel", type=int, default=1, 
                        help="Number of parallel processes (default: 1)")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    try:
        with open(args.dict, 'rb') as f:
            dictionary = f.read()
    except IOError as e:
        sys.stderr.write(f"Error reading dictionary: {e}\n")
        sys.exit(1)

    parser_inst = DumpParser(dictionary, args.filter)
    out_f = open(args.output, 'w') if args.output else sys.stdout
    
    stats = {
        'msg_count': 0,
        'bytes_processed': 0,
        'errors': 0,
        'start_time': time.time()
    }

    try:
        if args.parallel > 1:
            # Parallel processing logic
            pool = multiprocessing.Pool(processes=args.parallel)
            packet_gen = parser_inst.stream_packets(args.data)
            
            chunk_size = 1000
            while True:
                chunks = []
                try:
                    for _ in range(args.parallel):
                        batch = [next(packet_gen) for _ in range(chunk_size)]
                        chunks.append((dictionary, batch, args.filter))
                        stats['bytes_processed'] += sum(len(p) for p in batch)
                except StopIteration:
                    if batch:
                        chunks.append((dictionary, batch, args.filter))
                        stats['bytes_processed'] += sum(len(p) for p in batch)
                
                if not chunks:
                    break
                
                for result_batch in pool.map(process_chunk, chunks):
                    for msg in result_batch:
                        if args.json:
                            out_f.write(json.dumps({'msg': msg}) + '\n')
                        else:
                            out_f.write(msg + '\n')
                        stats['msg_count'] += 1
                
                if len(chunks) < args.parallel:
                    break
            pool.close()
            pool.join()
        else:
            # Serial processing (Optimized with generator)
            for packet in parser_inst.stream_packets(args.data):
                stats['bytes_processed'] += len(packet)
                msgs = parser_inst.parse_packet(packet)
                for msg in msgs:
                    if args.json:
                        out_f.write(json.dumps({'msg': msg}) + '\n')
                    else:
                        out_f.write(msg + '\n')
                    stats['msg_count'] += 1

        if args.stats:
            duration = time.time() - stats['start_time']
            sys.stderr.write("\n--- TUXEDO_RT Performance Statistics ---\n")
            sys.stderr.write(f"Messages parsed: {stats['msg_count']}\n")
            sys.stderr.write(f"Bytes processed: {stats['bytes_processed']}\n")
            sys.stderr.write(f"Processing time: {duration:.4f}s\n")
            sys.stderr.write(f"Throughput: {stats['bytes_processed']/duration/1024/1024:.2f} MB/s\n")

    finally:
        if args.output:
            out_f.close()

if __name__ == '__main__':
    main()
