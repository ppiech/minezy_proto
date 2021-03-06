module.exports = function(grunt) {

  grunt.initConfig({
    pkg: grunt.file.readJSON('package.json'),
    concat: {
      options: {
        separator: ';'
      },
      dist: {
        src: [
                'js/utils/Utils_h.js',
                'js/App.js'
        ],
        dest: '../static/dist/www/core.js'
      },
    },
    uglify: {
      dist: {
        options: {
          banner: '/*! <%= pkg.name %> <%= grunt.template.today("dd-mm-yyyy") %> */\n',
          sourceMap: true
        },
        files: {
          '../static/dist/www/core.min.js': ['<%= concat.dist.dest %>']
        }
      },
    },
    jshint: {
      files: ['Gruntfile.js', 'js/App.js', 'js/utils/*.js'],
      options: {
        // options here to override JSHint defaults
        globals: {
          jQuery: true,
          console: true,
          module: true,
          document: true
        }
      }
    },
    sass: {
      dist: {
        files: {
          '../static/dist/www/core.css' : 'css/main.scss'
        }
      },
    },
    watch: {
      css: {
        files: 'css/**/*.scss',
        tasks: ['sass:dist']
      },
      js: {
        files: ['<%= jshint.files %>'],
        tasks: ['jshint','concat:dist','uglify']
      }
    }
  });

  grunt.loadNpmTasks('grunt-contrib-uglify');
  grunt.loadNpmTasks('grunt-contrib-jshint');
  grunt.loadNpmTasks('grunt-contrib-watch');
  grunt.loadNpmTasks('grunt-contrib-concat');
  grunt.loadNpmTasks('grunt-contrib-sass');


};