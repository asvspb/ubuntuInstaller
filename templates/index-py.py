#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Sample Python Template

This is a sample Python template for the Ubuntu Installer Framework.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    """
    Main application entry point
    """
    logger.info("Ubuntu Installer Framework Python Template")
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Sample Python Template")
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose output"
    )
    parser.add_argument(
        "--config",
        "-c",
        type=str,
        help="Path to configuration file"
    )
    
    args = parser.parse_args()
    
    # Adjust logging level based on verbosity
    if args.verbose:
        logger.setLevel(logging.DEBUG)
        logger.debug("Verbose mode enabled")
    
    # Load configuration if provided
    if args.config:
        config_path = Path(args.config)
        if config_path.exists():
            logger.info(f"Loading configuration from {config_path}")
            load_configuration(config_path)
        else:
            logger.warning(f"Configuration file {config_path} not found")
    
    # Example of file operations
    example_operations()
    
    logger.info("Template execution completed successfully")

def load_configuration(config_path):
    """
    Load configuration from file
    
    Args:
        config_path (Path): Path to configuration file
    """
    logger.debug(f"Loading configuration from {config_path}")
    
    # In a real application, you would parse the configuration file here
    # For example, if it's a YAML file:
    # import yaml
    # with open(config_path, 'r') as f:
    #     config = yaml.safe_load(f)
    #     logger.debug(f"Configuration loaded: {config}")
    
    # Or if it's a JSON file:
    # import json
    # with open(config_path, 'r') as f:
    #     config = json.load(f)
    #     logger.debug(f"Configuration loaded: {config}")

def example_operations():
    """
    Example operations demonstrating common tasks
    """
    logger.info("Performing example operations")
    
    # Example of directory creation
    example_dir = Path.home() / "example"
    if not example_dir.exists():
        logger.debug(f"Creating directory {example_dir}")
        example_dir.mkdir(parents=True, exist_ok=True)
    
    # Example of file creation
    example_file = example_dir / "example.txt"
    if not example_file.exists():
        logger.debug(f"Creating file {example_file}")
        example_file.write_text("This is an example file created by the Python template.")
    
    # Example of file reading
    content = example_file.read_text()
    logger.debug(f"File content: {content}")
    
    # Example of environment variable access
    home_dir = os.environ.get("HOME", "Not set")
    logger.debug(f"Home directory: {home_dir}")
    
    # Example of system information
    logger.debug(f"Platform: {sys.platform}")
    logger.debug(f"Python version: {sys.version}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Operation cancelled by user")
        sys.exit(130)  # Standard exit code for Ctrl+C
    except Exception as e:
        logger.error(f"An error occurred: {e}")
        sys.exit(1)